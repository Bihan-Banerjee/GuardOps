"""
cli/commands/db_cmd.py — `guardops db` (Phase 12).

Maintenance for the scan-metadata database:

  guardops db init                  Create the schema (also happens lazily on first write)
  guardops db prune                 Apply the retention policy from .guardops.yaml
  guardops db prune --keep-last 50  Keep only the 50 most recent runs
  guardops db prune --keep-days 30  Delete runs older than 30 days
  guardops db export                Dump the whole DB as JSON (to stdout)
  guardops db export -o dump.json   ...or to a file (bridge for the future dashboard)
"""

import json
from pathlib import Path

import click

from cli.utils.output import success, info
from cli.utils.config import resolve_metadata_db_path
from cli.commands._metadata_common import open_store, fail


@click.group("db")
def db_group():
    """Manage the scan-metadata database (init / prune / export)."""
    pass


@db_group.command("init")
def db_init():
    """Create the metadata database schema if it does not exist."""
    config, store = open_store()
    path = resolve_metadata_db_path(config)
    try:
        store.init_schema()
    except Exception as e:
        fail(f"Could not initialize metadata DB: {e}")
    success(f"Metadata DB ready at [cyan]{path}[/cyan]")


@db_group.command("prune")
@click.option("--keep-days", default=None, type=int, help="Delete runs older than N days.")
@click.option("--keep-last", default=None, type=int, help="Keep only the N most recent runs.")
def db_prune(keep_days, keep_last):
    """Delete old scan runs (findings cascade). Defaults to the config retention policy."""
    config, store = open_store()

    # Fall back to the configured retention policy when no flags are given.
    if keep_days is None and keep_last is None:
        meta = config.get("metadata", {})
        keep_days = meta.get("retention_days") or None
        keep_last = meta.get("retention_keep_last") or None

    if not keep_days and not keep_last:
        info(
            "No retention configured "
            "([cyan]metadata.retention_days[/cyan] / [cyan]metadata.retention_keep_last[/cyan] "
            "are 0). Nothing pruned."
        )
        return

    result = store.prune(keep_days=keep_days, keep_last=keep_last)
    if not result.success:
        fail(f"Prune failed: {result.error_message}")

    if result.runs_deleted == 0:
        info("Nothing to prune — no runs matched the retention policy.")
    else:
        success(
            f"Pruned [bold]{result.runs_deleted}[/bold] run(s) and "
            f"[bold]{result.findings_deleted}[/bold] finding(s)."
        )


@db_group.command("export")
@click.option("--output", "-o", default=None, type=click.Path(dir_okay=False),
              help="Write JSON to this file (default: stdout).")
@click.option("--to-s3", "to_s3", is_flag=True, default=False,
              help="Publish the export to S3 for the web dashboard (uses metadata.s3_* config).")
@click.option("--bucket", default=None,
              help="Override the S3 bucket (default: metadata.s3_bucket in .guardops.yaml).")
def db_export(output, to_s3, bucket):
    """Export the entire metadata DB as JSON (runs + findings + tool runs).

    With --to-s3 the dump is uploaded to s3://<bucket>/<prefix>/<project>/latest.json,
    which is the durable source the guardops.live dashboard reads (Phase 13)."""
    config, store = open_store()
    try:
        data = store.export_json()
    except Exception as e:
        fail(f"Could not export metadata DB: {e}")

    text = json.dumps(data, indent=2, default=str)
    wrote_something = False

    if output:
        Path(output).write_text(text, encoding="utf-8")
        success(
            f"Exported [bold]{len(data.get('runs', []))}[/bold] run(s) "
            f"to [cyan]{output}[/cyan]"
        )
        wrote_something = True

    if to_s3:
        _export_to_s3(config, data, bucket)
        wrote_something = True

    if not wrote_something:
        click.echo(text)


def _export_to_s3(config: dict, data: dict, bucket_override) -> None:
    """Upload an export dict to the configured S3 location. Exits 1 on misconfig."""
    from backend.metadata.s3_store import build_s3_key, put_export_to_s3

    meta = config.get("metadata", {}) or {}
    bucket = bucket_override or meta.get("s3_bucket", "")
    if not bucket:
        fail(
            "No S3 bucket configured. Set [cyan]metadata.s3_bucket[/cyan] in "
            ".guardops.yaml or pass [cyan]--bucket[/cyan]."
        )

    project = (config.get("project", {}) or {}).get("name", "")
    key = build_s3_key(meta.get("s3_prefix", "metadata"), project)
    try:
        uri = put_export_to_s3(data, bucket, key, region=meta.get("region", ""))
    except Exception as e:
        fail(f"S3 upload failed: {e}")

    success(
        f"Published [bold]{len(data.get('runs', []))}[/bold] run(s) to [cyan]{uri}[/cyan]"
    )
    info("The dashboard (metadata.backend=s3) will pick this up on its next refresh.")

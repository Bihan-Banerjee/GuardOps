"""
cli/commands/dashboard_cmd.py — `guardops dashboard` (Phase 13 + v1.0.0).

Two jobs:

  guardops dashboard                      Serve the web dashboard API locally
                                          (uvicorn → backend.dashboard.app:app).
  guardops dashboard snapshot [--to-s3]   Publish a static JSON snapshot of the API
                                          so the public SPA renders last-known data
                                          while the cluster is down (v1.0.0).

`dashboard` is a group with invoke_without_command=True: running it with no
subcommand keeps the original Phase 13 behaviour (start the server). The snapshot
subcommand needs neither uvicorn nor fastapi, so the optional-extra check only runs
on the server path.
"""

import importlib.util
import json
import sys
from pathlib import Path

import click

from cli.utils.output import console, error, header, info, success, warn
from cli.utils.config import load_config

# Stable, project-agnostic key for the public snapshot the SPA falls back to.
DEFAULT_SNAPSHOT_KEY = "dashboard/snapshot.json"


@click.group("dashboard", invoke_without_command=True)
@click.option("--host", default=None, help="Bind host (default: dashboard.host in config, or 0.0.0.0).")
@click.option("--port", default=None, type=int, help="Bind port (default: dashboard.port in config, or 8081).")
@click.option("--reload", "reload_", is_flag=True, default=False,
              help="Auto-reload on code changes (development only).")
@click.pass_context
def dashboard_command(ctx, host, port, reload_):
    """Serve the GuardOps web dashboard API (FastAPI) locally, or publish a snapshot."""
    # A subcommand (e.g. `snapshot`) handles its own work — don't start the server.
    if ctx.invoked_subcommand is not None:
        return

    if importlib.util.find_spec("uvicorn") is None or importlib.util.find_spec("fastapi") is None:
        # Escape the [ so Rich does not treat [dashboard] as a markup tag.
        error(
            "The dashboard needs the optional extra. Install it with:\n"
            "   [bold green]pip install 'guardops\\[dashboard]'[/bold green]"
        )
        sys.exit(1)

    config = load_config()
    dash = config.get("dashboard", {}) or {}
    host = host or dash.get("host", "0.0.0.0")
    port = port or int(dash.get("port", 8081))

    from backend.dashboard.settings import load_settings
    settings = load_settings(config)
    backend = (settings.config.get("metadata", {}) or {}).get("backend", "sqlite")

    header("GuardOps · Dashboard", f"Web dashboard API for {settings.project_name}")
    info(f"URL:          http://{host}:{port}")
    info(f"API base:     http://{host}:{port}/api/v1")
    info(f"OpenAPI docs: http://{host}:{port}/docs")
    info(f"Metadata:     backend=[cyan]{backend}[/cyan]")
    if settings.auth_enabled:
        info(f"Auth:         [cyan]{settings.auth_mode}[/cyan] (enabled)")
    else:
        warn(
            "Auth is DISABLED (no credential configured). For a public deployment set "
            "[cyan]GUARDOPS_DASHBOARD_TOKEN[/cyan] (or USER/PASSWORD for basic auth)."
        )
    console.print()

    import uvicorn
    uvicorn.run(
        "backend.dashboard.app:app",
        host=host,
        port=port,
        reload=reload_,
        log_level="info",
    )


@dashboard_command.command("snapshot")
@click.option("--out", "-o", "out", default=None, type=click.Path(dir_okay=False),
              help="Write the snapshot JSON to this file.")
@click.option("--to-s3", "to_s3", is_flag=True, default=False,
              help="Upload the snapshot to S3 for the public SPA fallback.")
@click.option("--bucket", default=None,
              help="Override the S3 bucket (default: metadata.s3_bucket in .guardops.yaml).")
@click.option("--key", default=None,
              help=f"Override the S3 key (default: {DEFAULT_SNAPSHOT_KEY}).")
def dashboard_snapshot(out, to_s3, bucket, key):
    """Build a static snapshot of the dashboard API.

    Captures the durable findings/runs/trends plus the last-known live state
    (metrics/runtime/quarantine/sync) into one JSON document keyed by API path.
    The public SPA (VITE_SNAPSHOT_URL) falls back to it when app.guardops.live is
    unreachable, so the dashboard works 24/7 from any device with no running cluster.

    Run this while the cluster is still up (e.g. just before night-shutdown) so the
    live panels reflect real state rather than 'offline'.
    """
    from backend.dashboard.settings import load_settings
    from backend.dashboard.snapshot import build_snapshot

    config = load_config()
    settings = load_settings(config)
    snapshot = build_snapshot(settings)

    text = json.dumps(snapshot, indent=2, default=str)
    runs = len((snapshot.get("data", {}).get("/api/v1/runs", {}) or {}).get("runs", []))
    wrote_something = False

    if out:
        Path(out).write_text(text, encoding="utf-8")
        success(f"Wrote dashboard snapshot ([bold]{runs}[/bold] run(s)) to [cyan]{out}[/cyan]")
        wrote_something = True

    if to_s3:
        _upload_snapshot(config, snapshot, bucket, key)
        wrote_something = True

    if not wrote_something:
        click.echo(text)


def _upload_snapshot(config: dict, snapshot: dict, bucket_override, key_override) -> None:
    """Upload the snapshot dict to S3. Exits 1 on misconfiguration/failure."""
    from backend.metadata.s3_store import put_export_to_s3

    meta = config.get("metadata", {}) or {}
    bucket = bucket_override or meta.get("s3_bucket", "")
    if not bucket:
        error(
            "No S3 bucket configured. Set [cyan]metadata.s3_bucket[/cyan] in "
            ".guardops.yaml or pass [cyan]--bucket[/cyan]."
        )
        sys.exit(1)

    key = key_override or DEFAULT_SNAPSHOT_KEY
    try:
        uri = put_export_to_s3(snapshot, bucket, key, region=meta.get("region", ""))
    except Exception as e:  # noqa: BLE001
        error(f"S3 upload failed: {e}")
        sys.exit(1)

    success(f"Published dashboard snapshot to [cyan]{uri}[/cyan]")
    info(
        "Point [cyan]VITE_SNAPSHOT_URL[/cyan] at this object's public URL so the SPA "
        "falls back to it when the live API is unreachable."
    )

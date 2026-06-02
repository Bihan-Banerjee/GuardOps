"""
cli/commands/history_cmd.py — `guardops history` (Phase 12).

Lists past scan runs recorded in the scan-metadata database — the answer to
"what have we scanned, and what was the verdict?". Each row is one `guardops scan`
or one scan step inside `guardops deploy`.

Exit code is always 0 (it's a read). An empty DB prints a hint, not an error.

Examples:
  guardops history
  guardops history -n 50 --project my-api
  guardops history --env prod --json-output
"""

import click
from rich import box
from rich.table import Table

from cli.utils.output import info, console
from cli.commands._metadata_common import open_store, fmt_ts, short, emit_json, fail


@click.command("history")
@click.option("--limit", "-n", default=20, show_default=True, type=int, help="Max runs to show.")
@click.option("--project", default=None, help="Filter by project name.")
@click.option("--env", default=None, help="Filter by environment (local/staging/prod).")
@click.option("--image", default=None, help="Filter by image reference.")
@click.option("--json-output", "json_output", is_flag=True, default=False,
              help="Print raw JSON instead of a table (for CI/scripts).")
def history_command(limit, project, env, image, json_output):
    """Show recent scan runs from the metadata database."""
    _, store = open_store()
    try:
        runs = store.list_runs(project=project, environment=env, image=image, limit=limit)
    except Exception as e:
        fail(f"Could not read scan history: {e}")

    if json_output:
        emit_json([r.to_dict() for r in runs])
        return

    if not runs:
        info("No scan runs recorded yet. Run [bold]guardops scan[/bold] to populate the metadata DB.")
        return

    console.print()
    console.rule(f"[bold]GuardOps [cyan]Scan History[/cyan][/bold]  |  {len(runs)} run(s)")

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold dim", padding=(0, 1))
    table.add_column("ID", justify="right", style="bold")
    table.add_column("When (UTC)", no_wrap=True)
    table.add_column("Project")
    table.add_column("Env")
    table.add_column("Image")
    table.add_column("SHA")
    table.add_column("Src")
    table.add_column("Crit", justify="right", style="red")
    table.add_column("High", justify="right", style="red")
    table.add_column("Med", justify="right", style="yellow")
    table.add_column("Low", justify="right", style="dim")
    table.add_column("Gate", justify="center")

    for r in runs:
        gate = "[red]BLOCK[/red]" if r.blocked else "[green]pass[/green]"
        table.add_row(
            str(r.id),
            fmt_ts(r.timestamp),
            short(r.project_name, 18),
            r.environment or "—",
            short(r.image_ref, 28),
            r.git_sha or "—",
            r.source,
            str(r.crit_count),
            str(r.high_count),
            str(r.medium_count),
            str(r.low_count),
            gate,
        )

    console.print(table)
    console.print()

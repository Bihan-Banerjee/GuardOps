"""
cli/commands/trends_cmd.py — `guardops trends` (Phase 12).

Per-day rollup of severity counts across scan runs — is our security posture
improving or drifting? One row per calendar day (UTC), oldest first.

Examples:
  guardops trends
  guardops trends --days 7 --project my-api
  guardops trends --env prod --json-output
"""

import click
from rich import box
from rich.table import Table

from cli.utils.output import info, console
from cli.commands._metadata_common import open_store, emit_json, fail


@click.command("trends")
@click.option("--days", default=30, show_default=True, type=int, help="How many days back to include.")
@click.option("--project", default=None, help="Filter by project name.")
@click.option("--env", default=None, help="Filter by environment (local/staging/prod).")
@click.option("--json-output", "json_output", is_flag=True, default=False,
              help="Print raw JSON instead of a table (for CI/scripts).")
def trends_command(days, project, env, json_output):
    """Show per-day severity counts over time."""
    _, store = open_store()
    try:
        points = store.severity_trends(project=project, environment=env, days=days)
    except Exception as e:
        fail(f"Could not read trends: {e}")

    if json_output:
        emit_json([p.to_dict() for p in points])
        return

    if not points:
        info(f"No scan history in the last {days} day(s).")
        return

    console.print()
    console.rule(f"[bold]GuardOps [cyan]Severity Trends[/cyan][/bold]  |  last {days} day(s)")

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold dim", padding=(0, 1))
    table.add_column("Date", no_wrap=True)
    table.add_column("Crit", justify="right", style="red")
    table.add_column("High", justify="right", style="red")
    table.add_column("Med", justify="right", style="yellow")
    table.add_column("Low", justify="right", style="dim")
    table.add_column("Total", justify="right", style="bold")
    table.add_column("Runs", justify="right", style="dim")

    for p in points:
        table.add_row(
            p.date,
            str(p.crit),
            str(p.high),
            str(p.medium),
            str(p.low),
            str(p.total),
            str(p.runs),
        )

    console.print(table)
    console.print()

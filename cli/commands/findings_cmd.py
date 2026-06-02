"""
cli/commands/findings_cmd.py — `guardops findings` (Phase 12).

Queries individual findings stored across scan runs — "show me every CRITICAL",
"where has CVE-2026-XXXX shown up", "what did trivy flag in this run". Without a
DB this used to require grepping JSON reports.

`--severity` is a minimum threshold (e.g. HIGH includes CRITICAL), matching the
scan gate's semantics.

Examples:
  guardops findings --severity HIGH
  guardops findings --cve CVE-2026-33186
  guardops findings --run 12 --tool trivy
  guardops findings --image my-api:abc123 --json-output
"""

import click
from rich import box
from rich.table import Table

from cli.utils.output import info, console
from cli.commands._metadata_common import (
    open_store, severity_cell, short, emit_json, fail,
)


@click.command("findings")
@click.option("--run", default=None, type=int, help="Only findings from this scan-run ID.")
@click.option("--severity", default=None,
              type=click.Choice(["LOW", "MEDIUM", "HIGH", "CRITICAL"], case_sensitive=False),
              help="Minimum severity (inclusive). HIGH also shows CRITICAL.")
@click.option("--tool", default=None, help="Filter by scanner (semgrep/bandit/trivy/...).")
@click.option("--cve", default=None, help="Filter by exact CVE id.")
@click.option("--image", default=None, help="Filter by image reference.")
@click.option("--limit", "-n", default=50, show_default=True, type=int, help="Max findings to show.")
@click.option("--json-output", "json_output", is_flag=True, default=False,
              help="Print raw JSON instead of a table (for CI/scripts).")
def findings_command(run, severity, tool, cve, image, limit, json_output):
    """Query stored findings by run, severity, tool, CVE, or image."""
    _, store = open_store()
    try:
        rows = store.query_findings(
            run_id=run, severity=severity, tool=tool, cve=cve, image=image, limit=limit
        )
    except Exception as e:
        fail(f"Could not read findings: {e}")

    if json_output:
        emit_json([f.to_dict() for f in rows])
        return

    if not rows:
        info("No findings match those filters.")
        return

    console.print()
    console.rule(f"[bold]GuardOps [cyan]Findings[/cyan][/bold]  |  {len(rows)} shown")

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold dim", padding=(0, 1))
    table.add_column("Run", justify="right", style="dim")
    table.add_column("Severity")
    table.add_column("Tool", style="dim")
    table.add_column("Rule / CVE")
    table.add_column("Location")
    table.add_column("Message")
    table.add_column("Fix", style="dim")

    for f in rows:
        location = f"{f.file_path}:{f.line_start}" if f.file_path else "—"
        rule_cve = f.cve or f.rule_id or "—"
        table.add_row(
            str(f.run_id),
            severity_cell(f.severity),
            short(f.tool, 12),
            short(rule_cve, 26),
            short(location, 30),
            short(f.message, 60),
            short(f.fix_guidance, 40) if f.fix_guidance else "—",
        )

    console.print(table)
    console.print()

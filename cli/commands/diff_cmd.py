"""
cli/commands/diff_cmd.py — `guardops diff` (Phase 12).

Regression detection between two scan runs: what findings are NEW, what got
FIXED. Findings are matched by a stable fingerprint (tool|rule|cve|file|severity)
so the same issue across runs is recognised.

Default behaviour with no flags: compare the latest run against the previous run
for the same project — the common "did this build get worse?" question.

Exit codes (designed as a CI regression gate):
  0 — no new CRITICAL/HIGH findings (improvements or new low-severity only)
  1 — at least one new CRITICAL or HIGH finding was introduced

Examples:
  guardops diff
  guardops diff --from 11 --to 14
  guardops diff --project my-api --json-output
"""

import sys

import click

from cli.utils.output import info, success, console, result_panel
from cli.commands._metadata_common import (
    open_store, severity_cell, severity_rank, short, emit_json, fail,
)

_BLOCKING = {"CRITICAL", "HIGH"}
# Large cap so we compare the full finding set of each run, not a page of it.
_ALL = 1_000_000


@click.command("diff")
@click.option("--from", "from_id", default=None, type=int,
              help="Baseline run ID (default: the run before --to for the same project).")
@click.option("--to", "to_id", default=None, type=int,
              help="Target run ID (default: the latest run).")
@click.option("--project", default=None,
              help="Scope the auto-selected runs to a project.")
@click.option("--json-output", "json_output", is_flag=True, default=False,
              help="Print raw JSON instead of a table (for CI/scripts).")
def diff_command(from_id, to_id, project, json_output):
    """Compare two scan runs and report new vs fixed findings."""
    _, store = open_store()

    try:
        # ── Resolve the target (newer) run ────────────────────────────────────
        if to_id is not None:
            to_run = store.get_run(to_id)
            if to_run is None:
                fail(f"Run #{to_id} not found. See [bold]guardops history[/bold].")
        else:
            recent = store.list_runs(project=project, limit=1)
            if not recent:
                info("No scan runs to diff yet. Run [bold]guardops scan[/bold] first.")
                return
            to_run = recent[0]

        # ── Resolve the baseline (older) run ──────────────────────────────────
        if from_id is not None:
            from_run = store.get_run(from_id)
            if from_run is None:
                fail(f"Run #{from_id} not found. See [bold]guardops history[/bold].")
        else:
            from_run = store.latest_run_before(
                run_id=to_run.id, project=project or to_run.project_name
            )
            if from_run is None:
                info(
                    f"Run #{to_run.id} is the first recorded run for this project — "
                    "no earlier run to compare against."
                )
                return

        to_findings = store.query_findings(run_id=to_run.id, limit=_ALL)
        from_findings = store.query_findings(run_id=from_run.id, limit=_ALL)
    except SystemExit:
        raise
    except Exception as e:
        fail(f"Could not compute diff: {e}")

    # ── Set-diff on fingerprints ──────────────────────────────────────────────
    to_fp = {f.fingerprint: f for f in to_findings}
    from_fp = {f.fingerprint: f for f in from_findings}
    new = sorted(
        (f for fp, f in to_fp.items() if fp not in from_fp),
        key=lambda f: severity_rank(f.severity), reverse=True,
    )
    fixed = sorted(
        (f for fp, f in from_fp.items() if fp not in to_fp),
        key=lambda f: severity_rank(f.severity), reverse=True,
    )
    new_blocking = [f for f in new if f.severity in _BLOCKING]
    regressed = bool(new_blocking)

    if json_output:
        emit_json({
            "from_run": from_run.to_dict(),
            "to_run": to_run.to_dict(),
            "new": [f.to_dict() for f in new],
            "fixed": [f.to_dict() for f in fixed],
            "new_blocking": len(new_blocking),
            "regressed": regressed,
        })
        if regressed:
            sys.exit(1)
        return

    # ── Human-readable output ─────────────────────────────────────────────────
    console.print()
    console.rule(
        f"[bold]GuardOps [cyan]Diff[/cyan][/bold]  |  "
        f"#{from_run.id} → #{to_run.id}  ({to_run.project_name})"
    )

    if not new and not fixed:
        success(f"No change in findings between run #{from_run.id} and #{to_run.id}.")
        console.print()
        return

    if new:
        console.print(f"\n  [bold red]NEW ({len(new)})[/bold red]")
        for f in new:
            _print_finding(f)

    if fixed:
        console.print(f"\n  [bold green]FIXED ({len(fixed)})[/bold green]")
        for f in fixed:
            _print_finding(f)

    console.print()
    if regressed:
        result_panel(
            "Regression detected",
            [
                f"[bold]{len(new_blocking)}[/bold] new CRITICAL/HIGH finding(s) introduced "
                f"since run #{from_run.id}.",
                f"New: {len(new)}   Fixed: {len(fixed)}",
                "Fix the new findings above before promoting this build.",
            ],
            style="red",
        )
        sys.exit(1)
    else:
        result_panel(
            "No blocking regressions",
            [
                f"New: {len(new)} (none CRITICAL/HIGH)   Fixed: {len(fixed)}",
                "Safe to proceed.",
            ],
            style="green",
        )


def _print_finding(f) -> None:
    location = f"{f.file_path}:{f.line_start}" if f.file_path else "—"
    rule_cve = f.cve or f.rule_id or "—"
    console.print(
        f"    {severity_cell(f.severity)}  "
        f"[dim]{short(f.tool, 10)}[/dim]  "
        f"{short(rule_cve, 24)}  "
        f"{short(f.message, 50)}  "
        f"[dim]{short(location, 30)}[/dim]"
    )

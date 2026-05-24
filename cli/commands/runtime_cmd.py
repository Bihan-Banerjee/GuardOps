"""
cli/commands/runtime_cmd.py

`guardops runtime-status` command — Phase 7.

Queries Loki for Falco runtime security alerts and renders them as a
Rich table. Falco watches for suspicious in-pod activity (shell spawns,
sensitive file reads, unexpected outbound connections) and ships
structured JSON to Loki via a Promtail DaemonSet.

This command gives operators a terminal-native view of runtime threats
without opening Grafana — useful in scripts, incident runbooks, and
post-deploy CI gates.

Design mirrors deploy_cmd.py:
  - Reads config via load_config()
  - Uses info/success/error/warn/console from cli.utils.output
  - All business logic delegated to backend.security.falco_reader
  - --fail-on flag enables use as a CI gate step (exit 1 on threshold breach)

Usage examples:
  guardops runtime-status
  guardops runtime-status --since 24h --namespace default
  guardops runtime-status --severity HIGH --tail 20
  guardops runtime-status --loki-url http://localhost:3100
  guardops runtime-status --fail-on CRITICAL   # CI gate
"""

import sys
import click
from rich.table import Table

from cli.utils.output import info, success, error, warn, console
from cli.utils.config import load_config
from backend.security.falco_reader import query_falco_alerts, FalcoQueryResult


# ── Command definition ────────────────────────────────────────────────────────

@click.command("runtime-status")
@click.option(
    "--since",
    default="1h",
    type=click.Choice(
        ["15m", "30m", "1h", "3h", "6h", "12h", "24h", "7d"],
        case_sensitive=False,
    ),
    show_default=True,
    help="Time window to query Loki.",
)
@click.option(
    "--namespace",
    default=None,
    metavar="NS",
    help="Filter alerts to a specific Kubernetes namespace.",
)
@click.option(
    "--severity",
    default="LOW",
    type=click.Choice(["LOW", "MEDIUM", "HIGH", "CRITICAL"], case_sensitive=False),
    show_default=True,
    help="Minimum alert severity to display. LOW = show all.",
)
@click.option(
    "--tail",
    default=50,
    type=int,
    show_default=True,
    help="Maximum number of alerts to display.",
)
@click.option(
    "--loki-url",
    default=None,
    metavar="URL",
    help=(
        "Loki base URL. Overrides monitoring.loki_url in .guardops.yaml. "
        "Defaults to http://localhost:3100 (port-forward target)."
    ),
)
@click.option(
    "--fail-on",
    default=None,
    type=click.Choice(["LOW", "MEDIUM", "HIGH", "CRITICAL"], case_sensitive=False),
    help=(
        "Exit code 1 if alerts at or above this severity are found. "
        "Intended for CI post-deploy gates — omit for interactive use."
    ),
)
def runtime_status_command(since, namespace, severity, tail, loki_url, fail_on):
    """
    Show Falco runtime security alerts from the cluster.

    Queries Loki for events captured by Falco inside running pods.
    Falco detects shell spawns, sensitive file reads, package manager
    execution, and unexpected outbound connections.

    \b
    Prerequisite — Loki must be reachable. Quickest way:
      kubectl port-forward svc/loki 3100:3100 -n monitoring

    \b
    Examples:
      guardops runtime-status                       # last 1h, all severities
      guardops runtime-status --since 24h           # last 24 hours
      guardops runtime-status --severity HIGH       # HIGH and CRITICAL only
      guardops runtime-status --namespace default   # one namespace
      guardops runtime-status --fail-on HIGH        # CI gate: exit 1 if HIGH+ found
    """
    config = load_config()

    # URL resolution priority: CLI flag > .guardops.yaml > default port-forward
    resolved_loki_url = (
        loki_url
        or config.get("monitoring", {}).get("loki_url", "").strip()
        or "http://localhost:3100"
    )

    # ── Header ────────────────────────────────────────────────────────────────
    console.print()
    title_parts = [
        "[bold]GuardOps [cyan]Runtime Status[/cyan][/bold]",
        f"last [cyan]{since}[/cyan]",
    ]
    if namespace:
        title_parts.append(f"ns=[cyan]{namespace}[/cyan]")
    if severity != "LOW":
        title_parts.append(f"min=[cyan]{severity}[/cyan]")
    console.rule(" — ".join(title_parts))

    info(f"Querying Loki at [cyan]{resolved_loki_url}[/cyan] ...")

    result = query_falco_alerts(
        loki_url=resolved_loki_url,
        time_window=since,
        namespace_filter=namespace,
        min_severity=severity,
        limit=tail,
    )

    # ── Error / skip handling ─────────────────────────────────────────────────
    if result.skipped:
        warn(result.skip_reason)
        _print_setup_hint()
        return

    if not result.success:
        error(f"Failed to query Loki: {result.error_message}")
        console.print()
        console.print("  [dim]Troubleshooting:[/dim]")
        console.print("  [dim]  1. kubectl get pods -n monitoring -l app=loki[/dim]")
        console.print("  [dim]  2. kubectl port-forward svc/loki 3100:3100 -n monitoring[/dim]")
        console.print("  [dim]  3. curl http://localhost:3100/ready[/dim]")
        sys.exit(1)

    # ── Summary line ──────────────────────────────────────────────────────────
    counts = result.severity_counts
    _print_summary_line(counts, since, result.query_duration_seconds)

    if not result.alerts:
        success(f"No Falco alerts in the last {since} — cluster runtime looks clean")
        if fail_on:
            success(f"Runtime gate passed — no {fail_on}+ alerts in the last {since}")
        return

    # ── Alert table ───────────────────────────────────────────────────────────
    _print_alerts_table(result, tail)

    # ── CI gate ───────────────────────────────────────────────────────────────
    if fail_on:
        if result.has_alerts_above(fail_on):
            error(
                f"Runtime gate FAILED — {fail_on}+ severity Falco alerts found. "
                "Investigate the alerts above before marking this environment safe."
            )
            sys.exit(1)
        else:
            success(
                f"Runtime gate passed — no {fail_on}+ severity alerts in the last {since}"
            )


# ── Print helpers ─────────────────────────────────────────────────────────────

def _print_summary_line(
    counts: dict[str, int],
    since: str,
    duration: float,
) -> None:
    """One-line severity summary matching the DAST summary style in deploy_cmd.py."""
    c, h, m, lo = counts["CRITICAL"], counts["HIGH"], counts["MEDIUM"], counts["LOW"]

    crit_str = f"[bold red]CRITICAL:{c}[/bold red]" if c else f"[dim]CRITICAL:{c}[/dim]"
    high_str = f"[yellow]HIGH:{h}[/yellow]"         if h else f"[dim]HIGH:{h}[/dim]"
    med_str  = f"[blue]MEDIUM:{m}[/blue]"            if m else f"[dim]MEDIUM:{m}[/dim]"
    low_str  = f"[dim]LOW:{lo}[/dim]"

    console.print(
        f"  Falco alerts ({since}) — "
        f"{crit_str}  {high_str}  {med_str}  {low_str}  "
        f"[dim](query: {duration:.1f}s)[/dim]"
    )
    console.print()


def _print_alerts_table(result: FalcoQueryResult, tail: int) -> None:
    """
    Renders Falco alerts as a Rich table, capped at `tail` rows.

    Shows: severity, rule name, pod, namespace, time.
    Prints the full output string of the most severe alert below the
    table so the operator can act without opening Grafana.
    """
    SEV_COLOURS = {
        "CRITICAL": "bold red",
        "HIGH":     "yellow",
        "MEDIUM":   "blue",
        "LOW":      "dim",
    }

    table = Table(
        show_header=True,
        header_style="bold",
        show_lines=False,
        pad_edge=False,
        box=None,
    )
    table.add_column("SEV",       width=8,  no_wrap=True)
    table.add_column("RULE",      width=42, no_wrap=True)
    table.add_column("POD",       width=28, no_wrap=True)
    table.add_column("NAMESPACE", width=14, no_wrap=True)
    table.add_column("TIME (UTC)", width=9,  no_wrap=True)

    shown = result.alerts[:tail]
    for alert in shown:
        colour      = SEV_COLOURS.get(alert.severity, "white")
        rule_label  = _truncate(alert.rule,     42)
        pod_label   = _truncate(alert.pod_name, 28)

        table.add_row(
            f"[{colour}]{alert.severity}[/{colour}]",
            rule_label,
            pod_label,
            alert.namespace,
            _short_time(alert.timestamp),
        )

    console.print(table)

    overflow = len(result.alerts) - tail
    if overflow > 0:
        console.print(
            f"\n  [dim]... and {overflow} more alert(s). "
            f"Use --tail {len(result.alerts)} to see all, "
            f"or open Grafana Explore for full context.[/dim]"
        )

    # Print full output of the top (most severe / most recent) alert.
    # This lets the operator understand the event without leaving the terminal.
    top = result.alerts[0]
    console.print()
    colour = SEV_COLOURS.get(top.severity, "white")
    console.print(
        f"  [bold]Top alert ({top.rule}):[/bold]"
    )
    console.print(f"  [{colour}]{top.output}[/{colour}]")
    console.print()
    console.print(
        "  [dim]Full Loki query: "
        'kubectl port-forward svc/loki 3100:3100 -n monitoring  '
        '→  {app="falco"}[/dim]'
    )
    console.print(
        "  [dim]Grafana:         "
        "Explore → Loki datasource → "
        '{app="falco"} | json | severity != "LOW"[/dim]'
    )
    console.print()


def _print_setup_hint() -> None:
    """Printed when Loki is not configured — guides the operator to set up Phase 7."""
    console.print()
    console.print("  [bold]To enable Phase 7 runtime security:[/bold]")
    console.print()
    console.print("  [dim]Option A — Automated (recommended):[/dim]")
    console.print("  [dim]  .\\scripts\\setup-runtime-security.ps1[/dim]")
    console.print()
    console.print("  [dim]Option B — Terraform (Falco + Loki + Promtail):[/dim]")
    console.print("  [dim]  Set enable_runtime_security = true in terraform.tfvars[/dim]")
    console.print("  [dim]  terraform apply[/dim]")
    console.print()
    console.print("  [dim]Then add to .guardops.yaml:[/dim]")
    console.print("  [dim]  monitoring:[/dim]")
    console.print("  [dim]    loki_url: 'http://localhost:3100'[/dim]")
    console.print()


def _truncate(s: str, max_len: int) -> str:
    """Shortens a string to max_len with '..' suffix for table display."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 2] + ".."


def _short_time(ts: str) -> str:
    """
    Extracts HH:MM:SS from an ISO-8601 timestamp for compact table display.
    "2024-06-15T09:23:11Z" → "09:23:11"
    """
    if "T" in ts and len(ts) >= 19:
        return ts[11:19]
    return ts

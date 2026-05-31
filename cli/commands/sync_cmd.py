"""
cli/commands/sync_cmd.py

`guardops sync-status` — Phase 10 command.

Queries the ArgoCD REST API to display the current sync and health
status of a GuardOps Application in a Rich table. Optionally waits
for the Application to reach Synced + Healthy before returning, making
it usable as a blocking CI gate after a GitOps image promotion.

Usage:
  guardops sync-status --env prod
  guardops sync-status --env staging
  guardops sync-status --env prod --wait --timeout 300

Connection config is read from .guardops.yaml (argocd.url, argocd.app_name_<env>).
The ArgoCD API token is read from the environment variable named by
argocd.token_env_var (default: ARGOCD_TOKEN — matches the GitHub secret name).

Exit codes:
  0 — Application is Synced + Healthy (or --wait reached that state)
  1 — Application is Degraded, ArgoCD unreachable, config missing, or
      --wait timed out before reaching Healthy

Phase 10 — new command. Registered in cli/main.py as "sync-status".
"""

import os
import sys

import click
from rich.table import Table

from cli.utils.output import info, success, error, warn, console
from cli.utils.config import (
    load_config,
    get_env_config,
    get_argocd_url,
    get_argocd_app_name,
    get_argocd_token_env_var,
    resolve_domain,
)
from backend.pipeline.gitops_writer import (
    get_app_status,
    poll_until_healthy,
    SyncResult,
)


@click.command("sync-status")
@click.option(
    "--env",
    required=True,
    type=click.Choice(["staging", "prod"], case_sensitive=False),
    help="Environment whose ArgoCD Application to inspect.",
)
@click.option(
    "--wait",
    is_flag=True,
    default=False,
    help=(
        "Block until the Application reaches Synced + Healthy or --timeout. "
        "Exits 0 on success, 1 on timeout or Degraded. "
        "Designed for use as a CI gate step after triggering a GitOps deploy."
    ),
)
@click.option(
    "--timeout",
    default=300,
    type=int,
    show_default=True,
    help="Maximum seconds to wait when --wait is set (default: 300).",
)
@click.option(
    "--argocd-url",
    default=None,
    envvar="ARGOCD_URL",
    help=(
        "ArgoCD server URL. Overrides argocd.url in .guardops.yaml. "
        "Also readable from ARGOCD_URL env var. "
        "Example: https://argocd.guardops.live"
    ),
)
@click.option(
    "--token-env",
    default=None,
    help=(
        "Name of the env var holding the ArgoCD API token. "
        "Overrides argocd.token_env_var in .guardops.yaml. "
        "Default: ARGOCD_TOKEN"
    ),
)
def sync_status_command(env, wait, timeout, argocd_url, token_env):
    """
    Show ArgoCD Application sync and health status.

    \b
    Examples:
      guardops sync-status --env prod
      guardops sync-status --env staging
      guardops sync-status --env prod --wait --timeout 300
      guardops sync-status --env prod --argocd-url https://argocd.guardops.live
    """
    config = load_config()
    _     = get_env_config(config, env)   # validates env block exists

    # ── Resolve ArgoCD connection parameters ──────────────────────────────────
    # Priority: CLI flag > .guardops.yaml argocd section > DEFAULT_CONFIG

    url = argocd_url or get_argocd_url(config)
    if not url:
        error(
            "ArgoCD URL not configured. "
            "Set [cyan]argocd.url[/cyan] in [cyan].guardops.yaml[/cyan] "
            "or pass [cyan]--argocd-url https://argocd.guardops.live[/cyan].\n"
            "  After applying the argocd Terraform module: "
            "[dim]terraform output argocd_server_url[/dim]"
        )
        sys.exit(1)

    token_var = token_env or get_argocd_token_env_var(config)
    token     = os.environ.get(token_var, "")
    if not token:
        error(
            f"ArgoCD token not found in environment variable [cyan]{token_var}[/cyan].\n"
            f"  Set it with: [bold]export {token_var}=<your-argocd-api-token>[/bold]\n"
            f"  In CI: add [cyan]{token_var}[/cyan] as a GitHub secret."
        )
        sys.exit(1)

    app_name = get_argocd_app_name(config, env)
    domain   = resolve_domain(config, env)

    # ── Header ────────────────────────────────────────────────────────────────
    console.print()
    console.rule(
        f"[bold]GuardOps [cyan]Sync Status[/cyan][/bold]  |  "
        f"env=[cyan]{env}[/cyan]  |  "
        f"app=[cyan]{app_name}[/cyan]"
    )

    # ── Wait mode — block until Synced + Healthy ──────────────────────────────
    if wait:
        info(
            f"Waiting up to [cyan]{timeout}s[/cyan] for "
            f"[cyan]{app_name}[/cyan] → Synced + Healthy..."
        )
        result = poll_until_healthy(
            app_name=app_name,
            argocd_url=url,
            token=token,
            timeout_seconds=timeout,
            poll_interval=10,
        )

        _print_status_table(result, url, domain)

        if result.success:
            success(
                f"[cyan]{app_name}[/cyan] is Synced + Healthy "
                f"(took [cyan]{result.poll_duration_seconds:.0f}s[/cyan])"
            )
        else:
            error(f"Sync gate FAILED: {result.error_message}")
            _print_debug_hints(env, app_name)
            sys.exit(1)

        return   # exit 0

    # ── Snapshot mode — one-shot status check ─────────────────────────────────
    result = get_app_status(app_name, url, token)

    if not result.success:
        error(
            f"Failed to query ArgoCD: {result.error_message}\n"
            f"  Check that [cyan]{url}[/cyan] is reachable and the token is valid."
        )
        sys.exit(1)

    _print_status_table(result, url, domain)

    # ── Gate logic for one-shot mode ──────────────────────────────────────────
    if result.health_status == "Degraded":
        error(
            f"Application [cyan]{app_name}[/cyan] is [red]Degraded[/red] — "
            "investigate immediately."
        )
        _print_debug_hints(env, app_name)
        sys.exit(1)

    if result.sync_status == "OutOfSync":
        warn(
            f"Application [cyan]{app_name}[/cyan] is [yellow]OutOfSync[/yellow] — "
            "ArgoCD has not applied the latest commit yet. "
            "Run [bold]guardops sync-status --env {env} --wait[/bold] to poll until resolved."
        )
        # OutOfSync is transient during a rolling deploy — don't fail here

    elif result.sync_status == "Synced" and result.health_status == "Healthy":
        success(
            f"Application [cyan]{app_name}[/cyan] is "
            f"[green]Synced + Healthy[/green] ✓"
        )

    else:
        info(
            f"Application is in progress — "
            f"sync=[cyan]{result.sync_status}[/cyan]  "
            f"health=[cyan]{result.health_status}[/cyan]"
        )

    info(
        f"ArgoCD UI: [cyan]{url}/applications/{app_name}[/cyan]"
    )


# ── Display helpers ───────────────────────────────────────────────────────────

def _print_status_table(result: SyncResult, argocd_url: str, domain: str) -> None:
    """Renders a Rich table with the full ArgoCD Application status."""
    sync_color = (
        "green"  if result.sync_status   == "Synced"    else
        "red"    if result.sync_status   == "OutOfSync" else
        "yellow"
    )
    health_color = (
        "green"  if result.health_status == "Healthy"   else
        "red"    if result.health_status == "Degraded"  else
        "yellow"
    )

    table = Table(
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 2),
        min_width=50,
    )
    table.add_column("Field",  style="dim", width=20, no_wrap=True)
    table.add_column("Value",  min_width=30)

    table.add_row("Application",   f"[bold]{result.app_name}[/bold]")
    table.add_row(
        "Sync Status",
        f"[{sync_color}]{result.sync_status or 'Unknown'}[/{sync_color}]",
    )
    table.add_row(
        "Health Status",
        f"[{health_color}]{result.health_status or 'Unknown'}[/{health_color}]",
    )
    table.add_row("Revision",      result.revision or "—")
    table.add_row("ArgoCD UI",     f"[cyan]{argocd_url}/applications/{result.app_name}[/cyan]")

    if domain:
        table.add_row("Live URL",  f"[cyan]https://{domain}[/cyan]")

    if result.poll_duration_seconds:
        table.add_row("Wait time", f"{result.poll_duration_seconds:.0f}s")

    console.print()
    console.print(table)
    console.print()


def _print_debug_hints(env: str, app_name: str) -> None:
    """Prints kubectl/ArgoCD debug commands to help the operator investigate."""
    ns = "staging" if env == "staging" else "default"
    console.print()
    console.print("  [bold]Debug commands:[/bold]")
    console.print(f"    [dim]kubectl get pods -n {ns}[/dim]")
    console.print(f"    [dim]kubectl describe pods -n {ns}[/dim]")
    console.print(f"    [dim]kubectl logs -n {ns} -l app.kubernetes.io/name=guardops-app --tail=50[/dim]")
    console.print(f"    [dim]argocd app get {app_name}[/dim]")
    console.print(f"    [dim]argocd app history {app_name}[/dim]")
    console.print()

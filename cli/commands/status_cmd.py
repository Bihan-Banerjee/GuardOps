"""
cli/commands/status_cmd.py

Implements `guardops status`.

Shows the current state of the deployment:
  - Deployment status (desired vs ready replicas)
  - Pod list with health status
  - Recent Kubernetes events (for debugging)
"""

import click
from rich.table import Table
from rich import box

from cli.utils.output import (
    header, section, info, warn, blank, key_value_table, console
)
from cli.utils.config import load_config, get_project_name
from backend.pipeline.deployer import get_deployment_status, get_pods


@click.command()
@click.option(
    "--watch", "-w",
    is_flag=True,
    default=False,
    help="Watch for changes (refresh every 5 seconds). Press Ctrl+C to stop."
)
def status_command(watch):
    """
    Show deployment status and pod health.
    """
    config = load_config()
    project_name = get_project_name(config)
    namespace = config.get("kubernetes", {}).get("namespace", "default")

    if watch:
        import time
        try:
            while True:
                # Clear screen for watch mode
                console.clear()
                _print_status(project_name, namespace, config)
                console.print("[dim]  Refreshing every 5s. Press Ctrl+C to stop.[/dim]")
                time.sleep(5)
        except KeyboardInterrupt:
            info("Stopped watching.")
    else:
        _print_status(project_name, namespace, config)


def _print_status(project_name: str, namespace: str, config: dict) -> None:
    """Prints the full status view."""
    header(
        "GuardOps · Status",
        f"Project: {project_name}  |  Namespace: {namespace}"
    )

    # ── Deployment overview ───────────────────────────────────────────────────
    section("Deployment")
    status = get_deployment_status(project_name, namespace)

    if not status:
        warn(f"Deployment [bold]{project_name}[/bold] not found in namespace [bold]{namespace}[/bold].")
        info("Run [bold green]guardops deploy[/bold green] to deploy first.")
        return

    desired = status.get("desired_replicas", 0)
    ready = status.get("ready_replicas", 0)
    available = status.get("available_replicas", 0)

    # Choose color based on health: all ready = green, partial = yellow, none = red
    if ready == desired and desired > 0:
        health_color = "green"
        health_icon = "✓"
    elif ready > 0:
        health_color = "yellow"
        health_icon = "⚠"
    else:
        health_color = "red"
        health_icon = "✗"

    key_value_table(
        "Deployment Overview",
        {
            "Name":       status["name"],
            "Namespace":  status["namespace"],
            "Desired":    str(desired),
            "Ready":      f"[{health_color}]{health_icon} {ready}[/{health_color}]",
            "Available":  str(available),
        }
    )

    # ── Pod list ──────────────────────────────────────────────────────────────
    section("Pods")
    pods = get_pods(project_name, namespace)

    if not pods:
        warn("No pods found. They may still be starting.")
        return

    # Rich Table for a clean pod list
    pod_table = Table(
        box=box.SIMPLE,           # Minimal border style
        show_header=True,
        header_style="bold dim",
        padding=(0, 1),
    )
    pod_table.add_column("Pod Name",  style="cyan", no_wrap=True)
    pod_table.add_column("Phase",     justify="center")
    pod_table.add_column("Ready",     justify="center")
    pod_table.add_column("Restarts",  justify="right")
    pod_table.add_column("Node",      style="dim")

    for pod in pods:
        phase = pod.get("phase", "Unknown")
        is_ready = pod.get("ready", False)
        restarts = pod.get("restarts", 0)

        # Color-code the phase
        phase_display = {
            "Running":   f"[green]{phase}[/green]",
            "Pending":   f"[yellow]{phase}[/yellow]",
            "Failed":    f"[red]{phase}[/red]",
            "Succeeded": f"[blue]{phase}[/blue]",
        }.get(phase, phase)

        ready_display = "[green]Yes[/green]" if is_ready else "[red]No[/red]"
        restarts_display = str(restarts) if restarts == 0 else f"[yellow]{restarts}[/yellow]"

        pod_table.add_row(
            pod.get("name", ""),
            phase_display,
            ready_display,
            restarts_display,
            pod.get("node", ""),
        )

    console.print(pod_table)
    blank()
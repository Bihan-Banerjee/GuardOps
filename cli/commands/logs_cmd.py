"""
cli/commands/logs_cmd.py

Implements `guardops logs`.

Streams logs from the most recent pod for the project.
"""

import sys
import subprocess

import click

from cli.utils.output import header, info, error, warn, blank, console
from cli.utils.config import load_config, get_project_name
from backend.pipeline.deployer import get_pods


@click.command()
@click.option(
    "--tail", "-n",
    default=50,
    show_default=True,
    help="Number of recent lines to show before streaming."
)
@click.option(
    "--follow", "-f",
    is_flag=True,
    default=True,
    help="Stream logs in real time (default: on). Use --no-follow to print and exit."
)
@click.option(
    "--pod", "-p",
    default=None,
    help="Specific pod name. If not set, uses the most recent pod."
)
@click.option(
    "--previous",
    is_flag=True,
    default=False,
    help="Show logs from the previous (crashed) container. Useful for debugging crashes."
)
def logs_command(tail, follow, pod, previous):
    """
    Stream logs from your deployed application pods.
    """
    config = load_config()
    project_name = get_project_name(config)
    namespace = config.get("kubernetes", {}).get("namespace", "default")

    header("GuardOps · Logs", f"Project: {project_name}  |  Namespace: {namespace}")

    # ── Select pod ────────────────────────────────────────────────────────────
    if pod is None:
        pods = get_pods(project_name, namespace)
        if not pods:
            error(
                f"No pods found for [bold]{project_name}[/bold] in namespace [bold]{namespace}[/bold].\n"
                f"   Run [bold green]guardops deploy[/bold green] first."
            )
            sys.exit(1)
        # Use the first running pod (or just the first pod if none are running)
        running = [p for p in pods if p.get("phase") == "Running"]
        selected_pod = (running[0] if running else pods[0])["name"]
    else:
        selected_pod = pod

    info(f"Showing logs for pod [cyan]{selected_pod}[/cyan]")
    if follow:
        info("Press [bold]Ctrl+C[/bold] to stop streaming.")
    blank()

    # ── Build kubectl logs command ─────────────────────────────────────────────
    cmd = [
        "kubectl", "logs",
        selected_pod,
        "-n", namespace,
        f"--tail={tail}",
    ]

    if follow:
        cmd.append("--follow")   # Stream new logs as they arrive

    if previous:
        cmd.append("--previous") # Show logs from the crashed previous container

    # ── Stream logs ───────────────────────────────────────────────────────────
    # We use subprocess.run (not our wrapper) for streaming:
    # capture_output=False lets logs flow directly to the terminal in real time.
    # This is simpler than trying to intercept and re-color every log line.
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        blank()
        info("Log streaming stopped.")
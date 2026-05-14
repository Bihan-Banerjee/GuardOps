"""
cli/commands/rollback_cmd.py

`guardops rollback` command.

Wraps `helm rollback` to roll a release back to a previous revision.

Usage:
  guardops rollback                 # roll back to previous revision
  guardops rollback --revision 3    # roll back to a specific revision
  guardops rollback --history       # show release history, then exit

WHY HELM ROLLBACK IS POWERFUL:
  Every `guardops deploy` creates a new Helm revision. Helm stores the
  complete state (manifests + values) of every revision. Rolling back
  doesn't just restart pods — it restores the exact previous configuration,
  image tag, replica count, and all Helm values. It's a true state revert.
"""

import sys
import click
from rich.table import Table

from cli.utils.output import success, error, warn, console
from cli.utils.config import load_config
from backend.pipeline.deployer import rollback_helm, get_helm_history


@click.command("rollback")
@click.option("--revision", default=0, type=int,
              help="Helm revision to roll back to (default: previous revision)")
@click.option("--history", "show_history", is_flag=True,
              help="Show deployment history and exit without rolling back")
@click.option("--namespace", default=None,
              help="Kubernetes namespace (defaults to value in .guardops.yaml)")
@click.option("--release", default=None,
              help="Helm release name (defaults to project name)")
def rollback_command(revision, show_history, namespace, release):
    """
    Roll back to a previous deployment revision.

    Uses Helm's built-in rollback — restores the exact previous image,
    replica count, and all configuration values.

    Examples:\n
      guardops rollback               Roll back to previous revision\n
      guardops rollback --revision 3  Roll back to revision 3\n
      guardops rollback --history     Show all past revisions
    """
    config = load_config()

    project_name = config.get("project", {}).get("name", "guardops-app")
    ns = namespace or config.get("kubernetes", {}).get("namespace", "default")

    # Helm release name matches what deploy_helm() uses
    from backend.pipeline.deployer import _sanitize_release_name
    release_name = release or _sanitize_release_name(project_name)

    console.print()
    console.rule(
        f"[bold]GuardOps [cyan]Rollback[/cyan][/bold] — "
        f"release=[cyan]{release_name}[/cyan] | namespace=[cyan]{ns}[/cyan]"
    )

    # Always show history first so the user knows what they're rolling back to
    history = get_helm_history(release_name, namespace=ns)

    if not history:
        error(
            f"No Helm release found for '{release_name}' in namespace '{ns}'. "
            "Have you deployed with `guardops deploy` yet?"
        )
        sys.exit(1)

    _print_history_table(history)

    if show_history:
        return

    if len(history) < 2:
        warn("Only one revision exists — nothing to roll back to.")
        sys.exit(0)

    # Confirm before rolling back
    current = max(h.get("revision", 0) for h in history)
    target = revision if revision > 0 else current - 1

    console.print(
        f"\n  Rolling back [cyan]{release_name}[/cyan] "
        f"from revision [yellow]{current}[/yellow] "
        f"to revision [green]{target}[/green]...\n"
    )

    result = rollback_helm(release_name, namespace=ns, revision=revision)

    if not result.success:
        error(f"Rollback failed: {result.error_message}")
        sys.exit(1)

    success(
        f"Rolled back to revision [cyan]{result.rolled_back_to}[/cyan]. "
        "Run [bold]guardops status[/bold] to verify pods."
    )


def _print_history_table(history: list[dict]) -> None:
    """Prints Helm release history as a Rich table."""
    table = Table(show_header=True, header_style="bold dim")
    table.add_column("Revision", style="cyan", width=10)
    table.add_column("Updated", width=25)
    table.add_column("Status", width=12)
    table.add_column("Chart", width=20)
    table.add_column("Description")

    for entry in sorted(history, key=lambda x: x.get("revision", 0), reverse=True):
        status = entry.get("status", "")
        status_color = {
            "deployed": "green",
            "superseded": "dim",
            "failed": "red",
            "pending-rollback": "yellow",
        }.get(status, "white")

        table.add_row(
            str(entry.get("revision", "")),
            entry.get("updated", ""),
            f"[{status_color}]{status}[/{status_color}]",
            entry.get("chart", ""),
            entry.get("description", ""),
        )

    console.print("\n  [bold]Release History[/bold]")
    console.print(table)
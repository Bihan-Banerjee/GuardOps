"""
cli/commands/deploy_cmd.py

Implements `guardops deploy`.

Orchestration flow:
  1. Load project config (.guardops.yaml)
  2. Check dependencies (docker, kubectl)
  3. Build Docker image
  4. [Phase 2+] Run security scans
  5. Deploy to local Kubernetes
  6. Show deployment results

This command is the "conductor" — it calls backend modules
and handles user-facing feedback. Business logic stays in backend/.
"""

import sys
import time
from pathlib import Path

import click
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from cli.utils.output import (
    header, section, success, info, warn, error, blank,
    result_panel, console
)
from cli.utils.config import load_config, get_project_name
from cli.utils.system import check_all_dependencies
from backend.pipeline.builder import build_image, image_exists_locally, get_image_size
from backend.pipeline.deployer import deploy_local


@click.command()
@click.option(
    "--tag", "-t",
    default=None,
    help="Docker image tag. Defaults to current git SHA or 'latest'."
)
@click.option(
    "--env", "-e",
    type=click.Choice(["local", "dev", "staging", "prod"]),
    default="local",
    help="Target environment."
)
@click.option(
    "--replicas", "-r",
    default=1,
    type=int,
    show_default=True,
    help="Number of pod replicas."
)
@click.option(
    "--skip-build",
    is_flag=True,
    default=False,
    help="Skip Docker build (use existing local image)."
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would happen without actually deploying."
)
def deploy_command(tag, env, replicas, skip_build, dry_run):
    """
    Build, scan, and deploy your application to Kubernetes.

    In Phase 1, deploys to a local cluster (k3d/minikube).
    """
    start_time = time.time()

    # ── Load config ───────────────────────────────────────────────────────────
    config = load_config()
    project_name = get_project_name(config)
    docker_config = config.get("docker", {})
    k8s_config = config.get("kubernetes", {})

    # Determine image tag: use provided --tag, or git SHA, or "latest"
    if tag is None:
        tag = _get_git_sha() or "latest"

    header(
        "GuardOps · Deploy",
        f"Project: {project_name}  |  Environment: {env}  |  Tag: {tag}"
    )

    if dry_run:
        warn("[bold]DRY RUN[/bold] — no changes will be made")
        blank()

    # ── Check dependencies ─────────────────────────────────────────────────────
    deps = [
        ("docker",  "Install Docker: https://docs.docker.com/get-docker/"),
        ("kubectl", "Install kubectl: https://kubernetes.io/docs/tasks/tools/"),
    ]
    if not check_all_dependencies(deps):
        sys.exit(1)

    # ── Phase 1 Step definitions ──────────────────────────────────────────────
    # Each step is a dict with: name, a callable (fn), and whether it's skippable.
    # This structure makes it easy to add/remove steps without changing the loop.
    dockerfile = docker_config.get("dockerfile", "Dockerfile")
    context = docker_config.get("context", ".")
    namespace = k8s_config.get("namespace", "default")
    image_name = docker_config.get("image_name", project_name)
    full_image_ref = f"{image_name}:{tag}"

    # ── Step 1: Docker Build ──────────────────────────────────────────────────
    section("Step 1 / 2 — Docker Build")

    if skip_build:
        if not image_exists_locally(full_image_ref):
            error(
                f"--skip-build was set but image [cyan]{full_image_ref}[/cyan] "
                "doesn't exist locally."
            )
            sys.exit(1)
        info(f"Skipping build. Using existing image [cyan]{full_image_ref}[/cyan]")
    elif dry_run:
        info(f"[DRY RUN] Would build: docker build -t {full_image_ref} -f {dockerfile} {context}")
    else:
        # Validate Dockerfile exists before starting the build
        dockerfile_path = Path(context) / dockerfile
        if not dockerfile_path.exists():
            error(
                f"Dockerfile not found at [cyan]{dockerfile_path}[/cyan].\n"
                "   Run [bold]guardops init[/bold] to create a sample Dockerfile."
            )
            sys.exit(1)

        build_result = build_image(
            project_name=project_name,
            dockerfile_path=str(dockerfile_path),
            build_context=context,
            image_tag=tag,
        )

        if not build_result.success:
            error(f"Docker build failed: {build_result.error_message}")
            sys.exit(1)

        img_size = get_image_size(full_image_ref)
        success(
            f"Built [cyan]{full_image_ref}[/cyan]"
            + (f" ({img_size})" if img_size else "")
            + f" in {build_result.build_duration_seconds:.1f}s"
        )

    blank()

    # ── Step 2: Deploy to Kubernetes ──────────────────────────────────────────
    section("Step 2 / 2 — Kubernetes Deploy")

    if dry_run:
        info(f"[DRY RUN] Would deploy {full_image_ref} to namespace '{namespace}'")
        info(f"[DRY RUN] Replicas: {replicas}")
    else:
        deploy_result = deploy_local(
            project_name=project_name,
            image_ref=full_image_ref,
            namespace=namespace,
            replicas=replicas,
        )

        if not deploy_result.success:
            error(f"Deployment failed: {deploy_result.error_message}")
            blank()
            info("Troubleshooting:")
            info("  guardops logs           → view pod logs")
            info("  guardops status         → check pod states")
            info("  kubectl describe pods   → detailed K8s events")
            sys.exit(1)

        success(f"Deployed [cyan]{project_name}[/cyan] to namespace [cyan]{namespace}[/cyan]")

    # ── Final output ──────────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    blank()

    output_lines = [
        f"[bold]Project:[/bold]    {project_name}",
        f"[bold]Image:[/bold]      {full_image_ref}",
        f"[bold]Namespace:[/bold]  {namespace}",
        f"[bold]Replicas:[/bold]   {replicas}",
    ]

    if not dry_run:
        service_url = deploy_result.service_url if not dry_run else "N/A"
        output_lines.append(f"[bold]Service URL:[/bold] {service_url}")
        output_lines.append(f"[bold]Duration:[/bold]   {elapsed:.1f}s")

    result_panel(
        "✓ Deployment complete" if not dry_run else "Dry run complete",
        output_lines,
        style="green" if not dry_run else "yellow"
    )

    if not dry_run:
        console.print("  Run [bold green]guardops status[/bold green] to check pod health")
        console.print("  Run [bold green]guardops logs[/bold green] to stream logs")
    blank()


def _get_git_sha() -> str:
    """
    Returns the short git SHA of the current commit.
    Used as the image tag in CI/CD so every build is uniquely identifiable.

    Returns empty string if not in a git repo.
    """
    from cli.utils.system import get_command_output
    sha = get_command_output(
        ["git", "rev-parse", "--short", "HEAD"],
        default=""
    )
    return sha
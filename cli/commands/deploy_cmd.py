import sys
import time
from pathlib import Path

import click
from rich.progress import Progress, SpinnerColumn, TextColumn

from cli.utils.output import (
    header, section, success, info, warn, error,
    blank, result_panel, console,
)
from cli.utils.config import load_config, get_project_name
from cli.utils.system import check_all_dependencies
from backend.pipeline.builder import build_image, image_exists_locally, get_image_size
from backend.pipeline.deployer import deploy_local
from backend.security.semgrep_runner import run_semgrep
from backend.security.bandit_runner import run_bandit
from backend.security.trivy_runner import run_trivy_image, run_trivy_filesystem
from backend.security.sonarqube_runner import run_sonarqube
from backend.security.report_generator import generate_report


@click.command()
@click.option("--tag", "-t", default=None)
@click.option("--env", "-e", type=click.Choice(["local", "dev", "staging", "prod"]), default="local")
@click.option("--replicas", "-r", default=1, type=int)
@click.option("--skip-build",  is_flag=True, default=False)
@click.option("--skip-scan",   is_flag=True, default=False, help="Bypass security scans (not recommended)")
@click.option("--fail-on",     default="HIGH", type=click.Choice(["LOW", "MEDIUM", "HIGH", "CRITICAL"]))
@click.option("--dry-run",     is_flag=True, default=False)
@click.option("--push",        is_flag=True, default=False, help="Push image to ECR after build (Phase 2)")
def deploy_command(tag, env, replicas, skip_build, skip_scan, fail_on, dry_run, push):
    """Build, scan, and deploy your application to Kubernetes."""
    start_time = time.time()
    config = load_config()
    project_name = get_project_name(config)
    docker_config = config.get("docker", {})
    k8s_config = config.get("kubernetes", {})

    if tag is None:
        tag = _get_git_sha() or "latest"

    header(
        "GuardOps · Deploy",
        f"Project: {project_name}  |  Environment: {env}  |  Tag: {tag}"
    )

    if skip_scan:
        warn("Security scans are [bold]disabled[/bold]. Use only in development.")

    if dry_run:
        warn("[bold]DRY RUN[/bold] — no changes will be made")

    deps = [
        ("docker",  "Install Docker: https://docs.docker.com/get-docker/"),
        ("kubectl", "Install kubectl: https://kubernetes.io/docs/tasks/tools/"),
    ]
    if not check_all_dependencies(deps):
        sys.exit(1)

    dockerfile = docker_config.get("dockerfile", "Dockerfile")
    context    = docker_config.get("context", ".")
    namespace  = k8s_config.get("namespace", "default")
    image_name = docker_config.get("image_name", project_name)
    full_image_ref = f"{image_name}:{tag}"

    # ── Step 1: Docker Build ──────────────────────────────────────────────────
    section("Step 1 / 3 — Docker Build")

    if skip_build:
        if not image_exists_locally(full_image_ref):
            error(f"--skip-build set but image [cyan]{full_image_ref}[/cyan] not found locally.")
            sys.exit(1)
        info(f"Skipping build. Using existing image [cyan]{full_image_ref}[/cyan]")
    elif dry_run:
        info(f"[DRY RUN] Would build: docker build -t {full_image_ref} -f {dockerfile} {context}")
    else:
        dockerfile_path = Path(context) / dockerfile
        if not dockerfile_path.exists():
            error(f"Dockerfile not found at [cyan]{dockerfile_path}[/cyan].")
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

    # ── Step 2: Security Scans ────────────────────────────────────────────────
    section("Step 2 / 3 — Security Scans")

    if skip_scan:
        info("Scans skipped via --skip-scan flag")
    elif dry_run:
        info("[DRY RUN] Would run: Semgrep, Bandit, Trivy-fs, Trivy-image, SonarQube")
    else:
        scan_results = []

        info("Running Semgrep...")
        scan_results.append(run_semgrep(context, config))

        info("Running Bandit...")
        scan_results.append(run_bandit(context, config))

        info("Running Trivy filesystem scan...")
        scan_results.append(run_trivy_filesystem(context, config))

        info(f"Running Trivy container scan on [cyan]{full_image_ref}[/cyan]...")
        scan_results.append(run_trivy_image(full_image_ref, config))

        info("Running SonarQube...")
        scan_results.append(run_sonarqube(context, config))

        report = generate_report(
            scan_results=scan_results,
            project_name=project_name,
            image_ref=full_image_ref,
            fail_on_severity=fail_on,
        )

        counts = report.severity_counts
        info(
            f"Scan complete — "
            f"[red]Critical: {counts['CRITICAL']}[/red]  "
            f"[red]High: {counts['HIGH']}[/red]  "
            f"[yellow]Medium: {counts['MEDIUM']}[/yellow]  "
            f"Low: {counts['LOW']}"
        )

        if report.blocked:
            error(
                f"Deployment [bold]blocked[/bold] — {fail_on}+ severity findings detected.\n"
                f"   View full report: [cyan]security/reports/latest.html[/cyan]\n"
                f"   Run [bold]guardops scan[/bold] for detailed findings."
            )
            sys.exit(1)

        success("Security scans passed — no blocking findings")

    blank()

    # ── Step 3: Push to ECR (optional) ───────────────────────────────────────
    if push and not dry_run and not skip_build:
        section("Step 2.5 / 3 — Push to ECR")
        from backend.pipeline.pusher import push_to_ecr
        push_result = push_to_ecr(image_name, tag, config)
        if not push_result.success:
            error(f"ECR push failed: {push_result.error_message}")
            sys.exit(1)
        success(f"Pushed to [cyan]{push_result.image_uri}[/cyan]")
        full_image_ref = push_result.image_uri
        blank()

    # ── Step 3: Kubernetes Deploy ─────────────────────────────────────────────
    section("Step 3 / 3 — Kubernetes Deploy")

    if dry_run:
        info(f"[DRY RUN] Would deploy {full_image_ref} → namespace '{namespace}', replicas={replicas}")
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
            info("Run [bold]guardops status[/bold] and [bold]guardops logs[/bold] to debug.")
            sys.exit(1)

        success(f"Deployed [cyan]{project_name}[/cyan] to namespace [cyan]{namespace}[/cyan]")

    elapsed = time.time() - start_time
    blank()

    lines = [
        f"[bold]Project:[/bold]    {project_name}",
        f"[bold]Image:[/bold]      {full_image_ref}",
        f"[bold]Namespace:[/bold]  {namespace}",
        f"[bold]Replicas:[/bold]   {replicas}",
        f"[bold]Duration:[/bold]   {elapsed:.1f}s",
    ]
    if not dry_run and not skip_scan:
        lines.append(f"[bold]Scan report:[/bold] security/reports/latest.html")
    if not dry_run:
        lines.append(f"[bold]Service URL:[/bold] {deploy_result.service_url}")

    result_panel(
        "✓ Deployment complete" if not dry_run else "Dry run complete",
        lines,
        style="green" if not dry_run else "yellow",
    )

    if not dry_run:
        console.print("  Run [bold green]guardops status[/bold green] to verify pod health")
    blank()


def _get_git_sha() -> str:
    from cli.utils.system import get_command_output
    return get_command_output(["git", "rev-parse", "--short", "HEAD"], default="")
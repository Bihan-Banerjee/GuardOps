"""
cli/commands/deploy_cmd.py

`guardops deploy` command — Phase 3 version.

Flow:
  Step 1: Docker build
  Step 2: Security scans (semgrep, bandit, trivy-fs, trivy image, sonarqube)
          -> blocks if HIGH+ findings unless --skip-scan
  Step 3: ECR push (only when --env prod)
  Step 4: Helm deploy (both local and prod)

Environments:
  --env local  Uses k3d cluster, imagePullPolicy=Never, values.yaml
  --env prod   Pushes to ECR, uses EKS cluster, imagePullPolicy=Always,
               values.yaml + values-prod.yaml
"""

import sys
import time
import click

from cli.utils.output import (
    info, success, error, warn, result_panel, console
)
from cli.utils.config import load_config
from backend.pipeline.builder import build_image
from backend.pipeline.deployer import deploy_helm, import_image_to_k3d
from backend.pipeline.pusher import push_to_ecr
from backend.security.semgrep_runner import run_semgrep
from backend.security.bandit_runner import run_bandit
from backend.security.trivy_runner import run_trivy_image, run_trivy_filesystem
from backend.security.sonarqube_runner import run_sonarqube
from backend.security.report_generator import generate_report


@click.command("deploy")
@click.option("--env", default="local",
              type=click.Choice(["local", "prod"], case_sensitive=False),
              help="Target environment: local (k3d) or prod (EKS)")
@click.option("--skip-scan", is_flag=True,
              help="Skip security scans (development only — never use in prod)")
@click.option("--skip-build", is_flag=True,
              help="Skip Docker build and use the existing image")
@click.option("--skip-sonarqube", is_flag=True,
              help="Skip SonarQube scan")
@click.option("--skip-trivy", is_flag=True,
              help="Skip Trivy scans")
@click.option("--fail-on", default="HIGH",
              type=click.Choice(["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                                case_sensitive=False),
              help="Minimum severity that blocks deployment")
@click.option("--replicas", default=None, type=int,
              help="Number of pod replicas (overrides values.yaml)")
def deploy_command(env, skip_scan, skip_build, skip_sonarqube,
                   skip_trivy, fail_on, replicas):
    """
    Build, scan, and deploy the application.

    Local (default):  guardops deploy
    Production:       guardops deploy --env prod
    Skip scans:       guardops deploy --skip-scan   (dev only)
    """
    start_time = time.time()
    config = load_config()

    project_name = config.get("project", {}).get("name", "guardops-app")
    namespace = config.get("kubernetes", {}).get("namespace", "default")

    # Determine replica count: CLI flag > values-prod.yaml default > 1
    replica_count = replicas or (2 if env == "prod" else 1)

    # Git SHA as image tag
    from cli.utils.system import get_command_output
    tag_result = get_command_output(["git", "rev-parse", "--short", "HEAD"])
    image_tag = tag_result.strip() if tag_result.strip() else "latest"

    console.print()
    console.rule(
        f"[bold]GuardOps [cyan]Deploy[/cyan][/bold] — "
        f"[dim]{project_name}[/dim] | env=[cyan]{env}[/cyan] | tag=[cyan]{image_tag}[/cyan]"
    )

    # ── Step 1: Docker Build ─────────────────────────────────────────────────
    console.rule("[bold]Step 1 / 4 — Docker Build[/bold]")

    if skip_build:
        warn("Skipping Docker build (--skip-build)")
        full_image_ref = f"{project_name}:{image_tag}"
    else:
        dockerfile = config.get("docker", {}).get("dockerfile", "Dockerfile")
        context = config.get("docker", {}).get("context", ".")

        build_result = build_image(
            project_name=project_name,
            dockerfile_path=dockerfile,
            build_context=context,
            image_tag=image_tag,
        )
        if not build_result.success:
            error(f"Build failed: {build_result.error_message}")
            sys.exit(1)

        full_image_ref = build_result.full_image_ref
        success(f"Built [cyan]{full_image_ref}[/cyan] in {build_result.build_duration_seconds:.1f}s")

    # ── Step 2: Security Scans ───────────────────────────────────────────────
    console.rule("[bold]Step 2 / 4 — Security Scans[/bold]")

    if skip_scan:
        warn("Skipping security scans (--skip-scan). NEVER use this in production.")
        scan_results = []
    else:
        scan_results = []
        source_path = config.get("docker", {}).get("context", ".")

        info("Running Semgrep...")
        scan_results.append(run_semgrep(source_path, config))

        info("Running Bandit...")
        scan_results.append(run_bandit(source_path, config))

        if not skip_trivy:
            info("Running Trivy filesystem scan...")
            scan_results.append(run_trivy_filesystem(source_path, config))

            info(f"Running Trivy container scan on {full_image_ref}...")
            scan_results.append(run_trivy_image(full_image_ref, config))

        if not skip_sonarqube:
            info("Running SonarQube...")
            scan_results.append(run_sonarqube(source_path, config))

        report_dir = config.get("security", {}).get(
            "report_dir", "security/reports"
        )
        report = generate_report(
            scan_results=scan_results,
            project_name=project_name,
            image_ref=full_image_ref,
            output_dir=report_dir,
            fail_on_severity=fail_on,
        )

        # Print scan summary table
        _print_scan_summary(scan_results, report)

        if report.blocked:
            error(f"Deployment blocked — {fail_on}+ severity findings detected.")
            console.print(f"  [dim]View full report: {report_dir}/latest.html[/dim]")
            console.print("  [dim]Run guardops scan for detailed findings.[/dim]")
            sys.exit(1)

        success("Security scans passed — no blocking findings")

    # ── Step 3: ECR Push (prod only) ─────────────────────────────────────────
    console.rule("[bold]Step 3 / 4 — Registry Push[/bold]")

    if env == "prod":
        info("Pushing image to ECR...")
        push_result = push_to_ecr(full_image_ref, config)
        if not push_result.success:
            error(f"ECR push failed: {push_result.error_message}")
            sys.exit(1)
        full_image_ref = push_result.remote_image_ref
        success(f"Pushed to ECR: [cyan]{full_image_ref}[/cyan]")
    else:
        # Local: import into k3d instead of pushing to a registry
        info("Importing image into k3d cluster...")
        cluster = config.get("kubernetes", {}).get("cluster_name", "guardops-local")
        if not import_image_to_k3d(full_image_ref, cluster_name=cluster):
            error("Failed to import image into k3d cluster.")
            sys.exit(1)
        success("Image imported into k3d")

    # ── Step 4: Helm Deploy ──────────────────────────────────────────────────
    console.rule("[bold]Step 4 / 4 — Helm Deploy[/bold]")

    deploy_result = deploy_helm(
        project_name=project_name,
        image_ref=full_image_ref,
        namespace=namespace,
        replicas=replica_count,
        env=env,
    )

    duration = time.time() - start_time

    if not deploy_result.success:
        error(f"Deployment failed: {deploy_result.error_message}")
        console.print("[dim]Helm automatically rolled back to the previous release.[/dim]")
        sys.exit(1)

    result_panel(
        title="Deployment complete",
        lines=[
            f"Project:       {project_name}",
            f"Image:         {full_image_ref}",
            f"Environment:   {env}",
            f"Namespace:     {namespace}",
            f"Replicas:      {replica_count}",
            f"Helm release:  {deploy_result.helm_release}",
            f"Helm revision: {deploy_result.helm_revision}",
            f"Duration:      {duration:.1f}s",
            f"Service URL:   {deploy_result.service_url}",
        ],
    )
    info("Run [bold]guardops status[/bold] to verify pod health")
    if env == "local":
        info(
            "Local access: add [cyan]127.0.0.1  test-app.local[/cyan] "
            "to your hosts file, then open http://test-app.local"
        )


def _print_scan_summary(scan_results, report):
    """Prints a Rich table summarising all scan tool results."""
    counts = report.severity_counts
    info(
        f"Scan complete — "
        f"Critical: {counts['CRITICAL']}  "
        f"High: {counts['HIGH']}  "
        f"Medium: {counts['MEDIUM']}  "
        f"Low: {counts['LOW']}"
    )
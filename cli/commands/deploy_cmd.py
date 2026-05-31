"""
cli/commands/deploy_cmd.py

`guardops deploy` command — Phase 10 version.

Flow:
  Step 1: Docker build
  Step 2: Security scans (semgrep, bandit, trivy-fs, trivy image, sonarqube)
          -> blocks if HIGH+ findings unless --skip-scan
  Step 3: ECR push (only when --env prod or --env staging)
  Step 4: Helm deploy  AND/OR  GitOps override commit (controlled by --gitops flag)
  Step 5: DAST — OWASP ZAP baseline scan (prod only)
          -> auto-rollback if CRITICAL findings detected

Phase 10 changes vs Phase 9:
  - New flag: --gitops (default False)
      When set, Step 4 additionally:
        a) writes values-override-<env>.yaml with the new image tag
        b) git-commits the override file with [skip ci] message
        c) pushes to origin/<branch> so ArgoCD picks it up
        d) triggers an explicit ArgoCD sync via the REST API (prod only —
           staging has auto-sync enabled so no explicit trigger needed)
      The direct Helm upgrade still runs alongside the GitOps push so
      the cluster reflects the new image immediately, not after ArgoCD's
      polling interval. ArgoCD then reconciles and takes over ownership.
  - New flag: --gitops-branch (default "main") — branch to commit the override to
  - New import: gitops_writer (write_image_override, commit_and_push,
                               trigger_argocd_sync)
  - New imports from config: get_argocd_url, get_argocd_app_name,
                             get_argocd_token_env_var, resolve_domain

Phase 9 changes vs Phase 8:
  - --env now accepts "local" | "staging" | "prod" (was "local" | "prod")
  - New flag: --slot [blue|green] — activates blue-green deploy mode
  - Environment-specific config resolution via get_env_config() / resolve_*()
  - Helm release name includes env suffix + slot suffix when applicable

Environments:
  --env local    k3d cluster, imagePullPolicy=Never, values.yaml, no ECR push
  --env staging  ECR push, staging namespace, values-staging.yaml, no DAST
  --env prod     ECR push, default namespace, values-prod.yaml, full DAST
"""

import os
import sys
import time
import click

from cli.utils.output import (
    info, success, error, warn, result_panel, console
)
from cli.utils.config import (
    load_config,
    get_env_config,
    resolve_namespace,
    resolve_image_tag,
    resolve_helm_release_name,
    get_argocd_url,
    get_argocd_app_name,
    get_argocd_token_env_var,
)
from backend.pipeline.builder import build_image
from backend.pipeline.deployer import (
    deploy_helm,
    import_image_to_k3d,
    rollback_helm,
    _sanitize_release_name,
)
from backend.pipeline.pusher import push_to_ecr
# ── Phase 10: GitOps writer ───────────────────────────────────────────────────
from backend.pipeline.gitops_writer import (
    write_image_override,
    commit_and_push,
    trigger_argocd_sync,
)
from backend.security.semgrep_runner import run_semgrep
from backend.security.bandit_runner import run_bandit
from backend.security.trivy_runner import run_trivy_image, run_trivy_filesystem
from backend.security.sonarqube_runner import run_sonarqube
from backend.security.report_generator import generate_report
from backend.security.zap_runner import run_zap_baseline, ZapScanResult


@click.command("deploy")
@click.option("--env", default="local",
              type=click.Choice(["local", "staging", "prod"], case_sensitive=False),
              help="Target environment: local (k3d), staging, or prod (EKS)")
@click.option("--slot", default=None,
              type=click.Choice(["blue", "green"], case_sensitive=False),
              help=(
                  "Blue-green slot to deploy into. When set, creates a slot-specific "
                  "Helm release (e.g. guardops-app-staging-blue). Use "
                  "'guardops switch --slot <slot>' to cut traffic over."
              ))
@click.option("--gitops", "use_gitops", is_flag=True, default=False,
              help=(
                  "GitOps mode: after the direct Helm deploy, write values-override-<env>.yaml, "
                  "commit it, push to origin, and trigger an ArgoCD sync. "
                  "Requires argocd.url in .guardops.yaml and ARGOCD_TOKEN env var. "
                  "Use with --env staging or --env prod only."
              ))
@click.option("--gitops-branch", default="main", show_default=True,
              help="Git branch to commit the image override to (default: main).")
@click.option("--skip-scan", is_flag=True,
              help="Skip security scans (development only — never use in prod)")
@click.option("--skip-build", is_flag=True,
              help="Skip Docker build and use the existing image")
@click.option("--skip-sonarqube", is_flag=True,
              help="Skip SonarQube scan")
@click.option("--skip-trivy", is_flag=True,
              help="Skip Trivy scans")
@click.option("--skip-dast", is_flag=True,
              help="Skip OWASP ZAP DAST scan. Never use in prod.")
@click.option("--fail-on", default="HIGH",
              type=click.Choice(["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                                case_sensitive=False),
              help="Minimum severity that blocks deployment (pre-deploy scans)")
@click.option("--replicas", default=None, type=int,
              help="Number of pod replicas (overrides values.yaml)")
def deploy_command(env, slot, use_gitops, gitops_branch,
                   skip_scan, skip_build, skip_sonarqube,
                   skip_trivy, skip_dast, fail_on, replicas):
    """
    Build, scan, and deploy the application.

    \b
    Examples:
      guardops deploy                            # local k3d
      guardops deploy --env staging              # staging namespace on EKS
      guardops deploy --env prod                 # production on EKS
      guardops deploy --env prod --gitops        # GitOps mode: commit + ArgoCD sync
      guardops deploy --env staging --slot blue  # blue slot (blue-green)
      guardops deploy --env staging --slot green # green slot (blue-green)
      guardops deploy --skip-scan                # dev only, skips all scans
      guardops deploy --skip-dast                # dev only, skips ZAP
    """
    start_time = time.time()
    config = load_config()

    # ── Validate --gitops is only used with cloud envs ────────────────────────
    if use_gitops and env == "local":
        error(
            "--gitops is only supported for --env staging and --env prod. "
            "Local k3d deployments use direct Helm — no GitOps commit needed."
        )
        sys.exit(1)

    # ── Resolve env-aware config ─────────────────────────────────────────────
    env_config = get_env_config(config, env)

    project_name = env_config.get("project", {}).get("name", "guardops-app")
    namespace    = resolve_namespace(config, env)

    # Explicit None check so `--replicas 0` (scale to zero) is honoured
    # rather than falling through to the env default.
    replica_count = replicas if replicas is not None else (2 if env == "prod" else 1)

    # ── Git SHA as image tag ─────────────────────────────────────────────────
    from cli.utils.system import get_command_output
    tag_result = get_command_output(["git", "rev-parse", "--short", "HEAD"])
    sha_tag    = tag_result.strip() if tag_result.strip() else "latest"

    image_tag = resolve_image_tag(sha_tag, env, config)

    # ── Helm release name ────────────────────────────────────────────────────
    helm_release_name = resolve_helm_release_name(project_name, env, config, slot)

    # ── Extra Helm --set values for blue-green ────────────────────────────────
    helm_extra_values: dict[str, str] = {}
    if slot:
        helm_extra_values["blueGreen.enabled"] = "true"
        helm_extra_values["blueGreen.slot"]    = slot

    # ── Header ───────────────────────────────────────────────────────────────
    console.print()
    header_parts = [
        "[bold]GuardOps [cyan]Deploy[/cyan][/bold]",
        f"[dim]{project_name}[/dim]",
        f"env=[cyan]{env}[/cyan]",
        f"ns=[cyan]{namespace}[/cyan]",
        f"tag=[cyan]{image_tag}[/cyan]",
    ]
    if slot:
        header_parts.append(f"slot=[cyan]{slot}[/cyan]")
    if use_gitops:
        header_parts.append("[cyan]gitops=on[/cyan]")
    console.rule(" | ".join(header_parts))

    # ── Step 1: Docker Build ─────────────────────────────────────────────────
    console.rule("[bold]Step 1 / 5 — Docker Build[/bold]")

    if skip_build:
        warn("Skipping Docker build (--skip-build)")
        full_image_ref = f"{project_name}:{image_tag}"
    else:
        dockerfile = env_config.get("docker", {}).get("dockerfile", "Dockerfile")
        context    = env_config.get("docker", {}).get("context", ".")

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
    console.rule("[bold]Step 2 / 5 — Security Scans (SAST)[/bold]")

    if skip_scan:
        warn("Skipping security scans (--skip-scan). NEVER use this in production.")
        scan_results = []
    else:
        scan_results = []
        source_path = env_config.get("docker", {}).get("context", ".")

        effective_fail_on = fail_on
        if not fail_on or fail_on == "HIGH":
            effective_fail_on = env_config.get("security", {}).get("fail_on_severity", "HIGH")

        info("Running Semgrep...")
        scan_results.append(run_semgrep(source_path, env_config))

        info("Running Bandit...")
        scan_results.append(run_bandit(source_path, env_config))

        if not skip_trivy:
            info("Running Trivy filesystem scan...")
            scan_results.append(run_trivy_filesystem(source_path, env_config))

            info(f"Running Trivy container scan on {full_image_ref}...")
            scan_results.append(run_trivy_image(full_image_ref, env_config))

        if not skip_sonarqube:
            info("Running SonarQube...")
            scan_results.append(run_sonarqube(source_path, env_config))

        report_dir = env_config.get("security", {}).get("report_dir", "security/reports")
        report = generate_report(
            scan_results=scan_results,
            project_name=project_name,
            image_ref=full_image_ref,
            output_dir=report_dir,
            fail_on_severity=effective_fail_on,
        )

        _print_scan_summary(scan_results, report)

        if report.blocked:
            error(f"Deployment blocked — {effective_fail_on}+ severity findings detected.")
            console.print(f"  [dim]View full report: {report_dir}/latest.html[/dim]")
            console.print("  [dim]Run guardops scan for detailed findings.[/dim]")
            sys.exit(1)

        success("Security scans passed — no blocking findings")

    # ── Step 3: Registry Push ────────────────────────────────────────────────
    console.rule("[bold]Step 3 / 5 — Registry Push[/bold]")

    if env in ("staging", "prod"):
        info(f"Pushing image to ECR ({env})...")
        push_result = push_to_ecr(full_image_ref, env_config)

        if not push_result.success:
            error(f"ECR push failed: {push_result.error_message}")
            sys.exit(1)

        full_image_ref = push_result.image_uri
        success(f"Pushed to ECR: [cyan]{full_image_ref}[/cyan]")

    else:
        info("Importing image into k3d cluster...")
        cluster = env_config.get("kubernetes", {}).get("cluster_name", "guardops-local")
        if not import_image_to_k3d(full_image_ref, cluster_name=cluster):
            error("Failed to import image into k3d cluster.")
            sys.exit(1)
        success("Image imported into k3d")

    # ── Step 4: Helm Deploy  +  GitOps Override (Phase 10) ───────────────────
    console.rule("[bold]Step 4 / 5 — Helm Deploy[/bold]")

    if slot:
        info(
            f"Blue-green mode — deploying [cyan]{slot}[/cyan] slot "
            f"as release [cyan]{helm_release_name}[/cyan]"
        )
        info(
            f"Run [bold]guardops switch --slot {slot} --env {env}[/bold] "
            f"to cut traffic to this slot."
        )

    deploy_result = deploy_helm(
        project_name=project_name,
        image_ref=full_image_ref,
        namespace=namespace,
        replicas=replica_count,
        env=env,
        release_name=helm_release_name,
        extra_set_values=helm_extra_values if helm_extra_values else None,
    )

    if not deploy_result.success:
        error(f"Deployment failed: {deploy_result.error_message}")
        console.print("[dim]Helm automatically rolled back to the previous release.[/dim]")
        sys.exit(1)

    success(
        f"Deployed [cyan]{deploy_result.helm_release}[/cyan] "
        f"revision [cyan]{deploy_result.helm_revision}[/cyan]"
    )

    # ── Phase 10: GitOps override commit ─────────────────────────────────────
    # Runs AFTER the direct Helm deploy succeeds. This means the cluster
    # already has the new image; the commit gives ArgoCD a source-of-truth
    # record and lets it take over future drift reconciliation.
    #
    # Skipped for local (no ArgoCD) and when --gitops is not set.
    # Skipped for blue-green slot deploys — the override file tracks the
    # current "main" image, not slot images which are managed separately.
    gitops_result = None
    if use_gitops and env in ("staging", "prod") and not slot:
        info("Writing GitOps image override...")
        try:
            override_path = write_image_override(env=env, image_ref=full_image_ref)
            gitops_result = commit_and_push(
                override_path=override_path,
                env=env,
                image_tag=image_tag,
                branch=gitops_branch,
            )
        except Exception as exc:
            # GitOps commit failure is a warning, not a hard failure —
            # the Helm deploy already succeeded so the app is running.
            warn(
                f"GitOps override commit failed: {exc}. "
                "The Helm deploy succeeded but ArgoCD will not auto-sync this release. "
                "Commit values-override-{env}.yaml manually to resolve."
            )
            gitops_result = None
        else:
            if gitops_result and not gitops_result.success:
                warn(
                    f"GitOps commit failed: {gitops_result.error_message}. "
                    "Helm deploy succeeded — cluster is running the new image."
                )

        # ── Trigger ArgoCD sync for prod (staging uses auto-sync) ────────────
        # Staging ArgoCD Application has automated.prune=true and selfHeal=true,
        # so the commit push alone is sufficient for staging.
        # Prod has auto-sync disabled — CI must trigger explicitly.
        if gitops_result and gitops_result.success and env == "prod":
            argocd_url = get_argocd_url(config)
            token_var  = get_argocd_token_env_var(config)
            token      = os.environ.get(token_var, "")
            app_name   = get_argocd_app_name(config, env)

            if argocd_url and token:
                info(f"Triggering ArgoCD sync for [cyan]{app_name}[/cyan]...")
                sync_trigger = trigger_argocd_sync(
                    app_name=app_name,
                    argocd_url=argocd_url,
                    token=token,
                )
                if not sync_trigger.success:
                    warn(
                        f"ArgoCD sync trigger failed: {sync_trigger.error_message}. "
                        "Run [bold]guardops sync-status --env prod --wait[/bold] manually."
                    )
            else:
                info(
                    "ArgoCD URL or token not configured — skipping explicit sync trigger. "
                    "Set [cyan]argocd.url[/cyan] in .guardops.yaml and export "
                    f"[cyan]{token_var}[/cyan] to enable."
                )

    # ── Step 5: DAST — OWASP ZAP ─────────────────────────────────────────────
    console.rule("[bold]Step 5 / 5 — DAST (OWASP ZAP)[/bold]")

    zap_result = _run_dast_step(
        config=env_config,
        env=env,
        skip_dast=skip_dast,
        service_url=deploy_result.service_url,
        project_name=project_name,
        namespace=namespace,
        helm_release_name=helm_release_name,
    )

    # ── Final result panel ───────────────────────────────────────────────────
    duration = time.time() - start_time

    dast_status = "skipped"
    if not zap_result.skipped:
        counts = zap_result.severity_counts
        dast_status = (
            f"CRITICAL:{counts['CRITICAL']} HIGH:{counts['HIGH']} "
            f"MEDIUM:{counts['MEDIUM']} LOW:{counts['LOW']}"
        )

    panel_lines = [
        f"Project:       {project_name}",
        f"Image:         {full_image_ref}",
        f"Environment:   {env}",
        f"Namespace:     {namespace}",
        f"Replicas:      {replica_count}",
        f"Helm release:  {deploy_result.helm_release}",
        f"Helm revision: {deploy_result.helm_revision}",
        f"DAST:          {dast_status}",
        f"Duration:      {duration:.1f}s",
        f"Service URL:   {deploy_result.service_url}",
    ]
    if slot:
        panel_lines.insert(3, f"Slot:          {slot}")
    if gitops_result and gitops_result.success and not gitops_result.skipped:
        panel_lines.append(f"GitOps commit: {gitops_result.commit_sha}")

    result_panel(title="Deployment complete", lines=panel_lines)

    info("Run [bold]guardops status[/bold] to verify pod health")

    if slot:
        info(
            f"To activate this slot: "
            f"[bold green]guardops switch --slot {slot} --env {env}[/bold green]"
        )
    if use_gitops and env in ("staging", "prod"):
        info(
            f"To verify ArgoCD reconciliation: "
            f"[bold green]guardops sync-status --env {env} --wait[/bold green]"
        )
    if env == "local":
        info(
            "Local access: add [cyan]127.0.0.1  test-app.local[/cyan] "
            "to your hosts file, then open http://test-app.local"
        )


# ── DAST Step ─────────────────────────────────────────────────────────────────

def _run_dast_step(
    config: dict,
    env: str,
    skip_dast: bool,
    service_url: str,
    project_name: str,
    namespace: str,
    helm_release_name: str = "",
) -> ZapScanResult:
    """
    Run the ZAP DAST step and handle the result, including auto-rollback.

    Returns the ZapScanResult so the caller can include counts in the final panel.
    Calls sys.exit(1) if CRITICAL findings are found and rollback is triggered.
    """
    security_cfg = config.get("security", {})
    zap_enabled  = security_cfg.get("tools", {}).get("owasp_zap", False)

    if skip_dast:
        warn("Skipping DAST (--skip-dast). NEVER use this in production.")
        return ZapScanResult(skipped=True, skip_reason="--skip-dast flag")

    if not zap_enabled:
        if env == "staging":
            info("DAST skipped for staging environment (expected — ZAP runs in prod only).")
        else:
            warn(
                "DAST skipped — set [cyan]security.tools.owasp_zap: true[/cyan] "
                "in .guardops.yaml to enable."
            )
        return ZapScanResult(
            skipped=True,
            skip_reason=f"security.tools.owasp_zap is false for env={env}",
        )

    if env not in ("prod",):
        warn(
            "DAST skipped for non-prod environment — ZAP needs a stable HTTP URL. "
            "DAST runs automatically on [cyan]--env prod[/cyan]."
        )
        return ZapScanResult(
            skipped=True,
            skip_reason=f"DAST only runs in prod environment (got env={env})",
        )

    target_url = (
        security_cfg.get("zap_target_url", "").strip()
        or service_url
    )

    if not target_url:
        warn(
            "DAST skipped — could not determine target URL. "
            "Set [cyan]security.zap_target_url[/cyan] in .guardops.yaml."
        )
        return ZapScanResult(
            skipped=True,
            skip_reason="No target URL available for DAST scan",
        )

    info(f"Running OWASP ZAP baseline scan against [cyan]{target_url}[/cyan]")
    info("This runs passively — no attack payloads sent. Timeout: 5 min.")

    report_dir = security_cfg.get("report_dir", "security/reports")
    zap_result = run_zap_baseline(
        target_url=target_url,
        config=config,
        output_dir=report_dir,
    )

    if not zap_result.success and not zap_result.skipped:
        warn(f"ZAP scan failed: {zap_result.error_message}")
        warn(
            "The application is deployed but DAST could not complete. "
            "Investigate manually before marking this release production-safe."
        )
        return zap_result

    if not zap_result.skipped:
        _print_dast_summary(zap_result)

    if zap_result.blocked:
        counts = zap_result.severity_counts
        error(
            f"DAST found [bold red]{counts['CRITICAL']} CRITICAL[/bold red] findings "
            f"on the live application — triggering automatic rollback."
        )
        console.print(f"  [dim]ZAP report: {zap_result.html_report_path}[/dim]")

        info("Rolling back Helm release...")
        release_name = helm_release_name or _sanitize_release_name(project_name)
        rollback_result = rollback_helm(
            release_name=release_name,
            namespace=namespace,
            revision=0,
        )

        if rollback_result.success:
            success(
                f"Rolled back to revision [cyan]{rollback_result.rolled_back_to}[/cyan]. "
                "The previous stable version is now serving traffic."
            )
        else:
            error(
                f"Rollback also failed: {rollback_result.error_message}. "
                "Manual intervention required — check `helm history` and `kubectl get pods`."
            )

        console.print(
            "\n  [bold red]DAST gate FAILED[/bold red] — "
            "review the ZAP report and fix findings before re-deploying."
        )
        sys.exit(1)

    if not zap_result.skipped and zap_result.success:
        success("DAST passed — no CRITICAL findings detected on live application")

    return zap_result


# ── Print Helpers ─────────────────────────────────────────────────────────────

def _print_scan_summary(scan_results, report):
    """Prints a Rich table summarising all SAST scan tool results."""
    counts = report.severity_counts
    info(
        f"Scan complete — "
        f"Critical: {counts['CRITICAL']}  "
        f"High: {counts['HIGH']}  "
        f"Medium: {counts['MEDIUM']}  "
        f"Low: {counts['LOW']}"
    )


def _print_dast_summary(zap_result: ZapScanResult) -> None:
    """Prints a concise DAST findings table to the terminal."""
    counts = zap_result.severity_counts
    info(
        f"DAST complete in {zap_result.scan_duration_seconds:.0f}s — "
        f"Critical: {counts['CRITICAL']}  "
        f"High: {counts['HIGH']}  "
        f"Medium: {counts['MEDIUM']}  "
        f"Low: {counts['LOW']}"
    )

    blocking = [
        f for f in zap_result.findings
        if f.severity in ("CRITICAL", "HIGH")
    ]
    if blocking:
        console.print()
        console.print("  [bold]Top DAST findings:[/bold]")
        for finding in blocking[:5]:
            sev_color = "red" if finding.severity == "CRITICAL" else "yellow"
            console.print(
                f"  [{sev_color}]{finding.severity:<8}[/{sev_color}] "
                f"{finding.alert} "
                f"[dim]({finding.instance_count} instance{'s' if finding.instance_count != 1 else ''})[/dim]"
            )
        if len(blocking) > 5:
            console.print(f"  [dim]... and {len(blocking) - 5} more. See full report.[/dim]")
        console.print(f"  [dim]Full report: {zap_result.html_report_path}[/dim]")
        console.print()

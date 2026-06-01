"""
backend/pipeline/gitops_writer.py

Phase 10: GitOps image override writer, git commit/push, and ArgoCD sync.

Responsibilities:
  1. write_image_override() — writes a values-override-<env>.yaml into the
     Helm chart directory containing only the new image.repository and
     image.tag fields. ArgoCD is configured to load this file on top of
     values-<env>.yaml so only the image changes on each deploy.

  2. commit_and_push() — git-commits the override file with a Conventional
     Commits message and pushes to origin. This is the GitOps trigger:
     ArgoCD watches the repo and auto-syncs when it detects a new commit
     (for staging) or waits for an explicit sync (for prod).

  3. trigger_argocd_sync() — calls the ArgoCD REST API to initiate an
     explicit sync. Used for prod where auto-sync is disabled so a human
     (or the CI sync-gate job) controls when ArgoCD applies the change.

  4. get_app_status() — single poll of the ArgoCD Application status.
     Returns sync_status (Synced | OutOfSync | Unknown) and health_status
     (Healthy | Degraded | Progressing | Unknown).

  5. poll_until_healthy() — repeatedly calls get_app_status() until the
     Application is Synced + Healthy, it enters Degraded state, or the
     timeout expires. Used by `guardops sync-status --wait` and the CI
     sync-gate job as a blocking gate.

WHY GITOPS (vs direct Helm):
  - Every image promotion is a git commit — full audit trail, easy rollback
    (revert the commit), and ArgoCD continuously reconciles drift.
  - Declarative: the cluster state is always derivable from files in version
    control. No "what Helm flags were used in the CI job?" ambiguity.
  - ArgoCD self-heals: if someone manually changes a resource in the cluster,
    ArgoCD reverts it to match Git within minutes.

Design decisions:
  - values-override-<env>.yaml lives in k8s/helm/guardops-app/.
    It is .gitignored for local dev but committed by CI in staging/prod.
    ArgoCD Application spec lists it as the last -f file so it wins.
  - Git identity is set to guardops-bot so machine commits are visually
    distinct from developer commits in `git log`.
  - [skip ci] in the commit message prevents the CI pipeline from
    triggering on its own GitOps commit (GitHub Actions ignores it).
  - The ArgoCD token is read from the environment — never from config files.
    Callers pass the token string; this module never calls os.environ directly
    so it remains testable without environment side-effects.
"""

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests

from cli.utils.output import info, success, warn


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GitOpsResult:
    """Result of a GitOps image override commit + push operation."""
    success: bool
    commit_sha: str = ""
    branch: str = ""
    override_file: str = ""
    error_message: str = ""
    # skipped=True means the file was written but nothing changed to commit
    # (image tag was already current). This is a success state, not a failure.
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class SyncResult:
    """Result of an ArgoCD Application status query or sync trigger."""
    success: bool
    app_name: str = ""
    # Values from the ArgoCD API status block
    sync_status: str = ""       # "Synced" | "OutOfSync" | "Unknown"
    health_status: str = ""     # "Healthy" | "Degraded" | "Progressing" | "Unknown"
    revision: str = ""          # Short git SHA currently deployed
    error_message: str = ""
    skipped: bool = False
    skip_reason: str = ""
    # Populated by poll_until_healthy() — 0.0 for single-shot status calls
    poll_duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_image_override(
    env: str,
    image_ref: str,
    chart_dir: Optional[Path] = None,
) -> Path:
    """
    Writes a values-override-<env>.yaml into the Helm chart directory.

    The file contains only image.repository, image.tag, and image.pullPolicy.
    ArgoCD loads this file last (after values.yaml and values-<env>.yaml) so
    its image fields take precedence over any image settings in those files.
    All other Helm values (resources, probes, HPA, monitoring …) are
    unaffected — they remain in values-<env>.yaml.

    This is the smallest possible diff on each promote:
      git diff HEAD~1 HEAD -- k8s/helm/guardops-app/values-override-prod.yaml
      -  tag: abc1234
      +  tag: def5678

    Args:
        env:       "staging" | "prod"
        image_ref: Full ECR image reference, e.g.
                   "123456789.dkr.ecr.ap-south-1.amazonaws.com/guardops-app:abc1234"
        chart_dir: Path to the Helm chart directory. Auto-detected if None.

    Returns:
        Path to the written override file.
    """
    if chart_dir is None:
        chart_dir = _find_chart_dir()

    override_path = chart_dir / f"values-override-{env}.yaml"
    repo, tag = _split_image_ref(image_ref)

    content = (
        f"# Auto-generated by guardops deploy — do not edit manually.\n"
        f"# Env: {env}  Tag: {tag}\n"
        f"# Updated by the GitOps pipeline. See backend/pipeline/gitops_writer.py.\n"
        f"image:\n"
        f"  repository: {repo}\n"
        f"  tag: \"{tag}\"\n"
        f"  pullPolicy: Always\n"
    )

    override_path.write_text(content, encoding="utf-8")
    info(f"Wrote image override → [cyan]{override_path}[/cyan] (tag: [cyan]{tag}[/cyan])")
    return override_path


def commit_and_push(
    override_path: Path,
    env: str,
    image_tag: str,
    branch: str = "main",
) -> GitOpsResult:
    """
    Git-commits the image override file and pushes it to origin.

    Uses guardops-bot as the git committer identity so machine-generated
    commits are visually distinct from developer commits in `git log`.

    Commit message format (Conventional Commits):
      chore(gitops): promote <env> image to <image_tag> [skip ci]

    The [skip ci] suffix prevents GitHub Actions from re-triggering the
    CI pipeline on this commit — we only want the ArgoCD reconciliation
    loop to pick it up, not run another full build.

    Idempotency: if the override file contains no new changes (the tag
    was already committed), this returns a GitOpsResult with skipped=True
    and success=True. The caller should treat this as a no-op success, not
    a failure — it just means a previous pipeline run already promoted this
    exact image.

    Args:
        override_path: Path to the values-override-<env>.yaml file.
        env:           "staging" | "prod"
        image_tag:     Short tag / SHA string for the commit message.
        branch:        Git branch to push to (default: "main").

    Returns:
        GitOpsResult with commit_sha on success, error_message on failure.
    """
    try:
        # Set bot identity (safe no-op if already set globally in CI environment)
        _run_git(["config", "user.email", "guardops-bot@users.noreply.github.com"])
        _run_git(["config", "user.name", "guardops-bot"])

        # Stage only the override file — never accidentally commit other changes
        _run_git(["add", str(override_path)])

        # Check if there is actually something staged to commit (idempotent path)
        check = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True,
        )
        if check.returncode == 0:
            # No diff → tag already committed → nothing to do
            info("GitOps: no changes to commit — image tag is already current in Git.")
            return GitOpsResult(
                success=True,
                commit_sha=_get_current_sha(),
                branch=branch,
                override_file=str(override_path),
                skipped=True,
                skip_reason=f"Image tag {image_tag!r} already committed for env={env}",
            )

        commit_msg = f"chore(gitops): promote {env} image to {image_tag} [skip ci]"
        _run_git(["commit", "-m", commit_msg])

        commit_sha = _get_current_sha()
        _run_git(["push", "origin", branch])

        success(
            f"GitOps commit [cyan]{commit_sha}[/cyan] pushed → "
            f"[cyan]origin/{branch}[/cyan]"
        )
        return GitOpsResult(
            success=True,
            commit_sha=commit_sha,
            branch=branch,
            override_file=str(override_path),
        )

    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else str(exc)
        return GitOpsResult(
            success=False,
            error_message=f"Git operation failed: {stderr}",
        )


def trigger_argocd_sync(
    app_name: str,
    argocd_url: str,
    token: str,
    revision: str = "HEAD",
    prune: bool = True,
) -> SyncResult:
    """
    Triggers an explicit sync on an ArgoCD Application via the REST API.

    This is the right call when ArgoCD auto-sync is disabled (prod) or when
    you want to force an immediate sync rather than waiting for the default
    ArgoCD polling interval (~3 minutes).

    After calling this, use poll_until_healthy() to block until the sync
    completes (or fails). trigger_argocd_sync() only confirms the API accepted
    the request — it does NOT wait for reconciliation to finish.

    API: POST /api/v1/applications/{name}/sync
    Ref: https://argo-cd.readthedocs.io/en/stable/developer-guide/api-docs/

    Args:
        app_name:   ArgoCD Application name, e.g. "guardops-app-prod".
        argocd_url: Base URL, e.g. "https://argocd.guardops.live".
        token:      ArgoCD API token — from ARGOCD_TOKEN env var in CI.
        revision:   Git revision to sync to (default: HEAD = latest commit).
        prune:      Remove resources not in Git on this sync (default: True).

    Returns:
        SyncResult where success=True means the API accepted the request,
        NOT that reconciliation succeeded. Use poll_until_healthy() for that.
    """
    url = f"{argocd_url.rstrip('/')}/api/v1/applications/{app_name}/sync"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "revision": revision,
        "prune": prune,
        "dryRun": False,
        "strategy": {"hook": {"force": False}},
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
    except requests.RequestException as exc:
        return SyncResult(
            success=False,
            app_name=app_name,
            error_message=f"ArgoCD API request failed: {exc}",
        )

    if resp.status_code in (200, 202):
        success(f"ArgoCD sync triggered for [cyan]{app_name}[/cyan]")
        return SyncResult(success=True, app_name=app_name)

    return SyncResult(
        success=False,
        app_name=app_name,
        error_message=(
            f"ArgoCD API returned HTTP {resp.status_code}: "
            f"{resp.text[:300]}"
        ),
    )


def get_app_status(
    app_name: str,
    argocd_url: str,
    token: str,
) -> SyncResult:
    """
    Fetches the current sync and health status of an ArgoCD Application.

    Called by poll_until_healthy() on each iteration and by
    `guardops sync-status` for a one-shot snapshot.

    sync_status values:   "Synced" | "OutOfSync" | "Unknown"
    health_status values: "Healthy" | "Degraded" | "Progressing" | "Suspended" | "Unknown"

    Args:
        app_name:   ArgoCD Application name.
        argocd_url: Base URL.
        token:      ArgoCD API token.

    Returns:
        SyncResult with sync_status, health_status, and revision populated.
    """
    url = f"{argocd_url.rstrip('/')}/api/v1/applications/{app_name}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        return SyncResult(
            success=False,
            app_name=app_name,
            error_message=f"ArgoCD API request failed: {exc}",
        )

    if resp.status_code != 200:
        return SyncResult(
            success=False,
            app_name=app_name,
            error_message=(
                f"ArgoCD API returned HTTP {resp.status_code}: "
                f"{resp.text[:200]}"
            ),
        )

    try:
        data   = resp.json()
        status = data.get("status", {})
        sync   = status.get("sync", {})
        health = status.get("health", {})
    except (ValueError, KeyError) as exc:
        return SyncResult(
            success=False,
            app_name=app_name,
            error_message=f"Could not parse ArgoCD response: {exc}",
        )

    return SyncResult(
        success=True,
        app_name=app_name,
        sync_status=sync.get("status", "Unknown"),
        health_status=health.get("status", "Unknown"),
        # Trim to short SHA for display; ArgoCD returns the full 40-char SHA
        revision=sync.get("revision", "")[:7],
    )


def poll_until_healthy(
    app_name: str,
    argocd_url: str,
    token: str,
    timeout_seconds: int = 300,
    poll_interval: int = 10,
) -> SyncResult:
    """
    Polls ArgoCD until the Application is Synced + Healthy or the timeout expires.

    Prints a progress line on each poll iteration so the operator can
    watch reconciliation progress in real time (useful both in CI logs
    and when run locally via `guardops sync-status --wait`).

    Terminal states (stops polling):
      - Synced + Healthy  → success=True
      - Degraded          → success=False (something went wrong, stop immediately)
      - Timeout           → success=False

    Non-terminal states (keeps polling):
      - OutOfSync + Progressing — ArgoCD is applying changes
      - Synced + Progressing    — resources are rolling out
      - API error               — warns and retries (transient network blip)

    Args:
        app_name:        ArgoCD Application name.
        argocd_url:      Base URL.
        token:           ArgoCD API token.
        timeout_seconds: Max wait time in seconds (default: 300 = 5 min).
        poll_interval:   Seconds between polls (default: 10).

    Returns:
        SyncResult with the final health + sync state and poll_duration_seconds.
    """
    start       = time.monotonic()
    last_status = SyncResult(success=False, app_name=app_name)

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout_seconds:
            last_status.success = False
            last_status.error_message = (
                f"Timeout after {timeout_seconds}s — "
                f"app did not reach Synced + Healthy"
            )
            last_status.poll_duration_seconds = elapsed
            return last_status

        last_status = get_app_status(app_name, argocd_url, token)

        if not last_status.success:
            # Transient API error — warn and retry rather than failing hard
            warn(f"[{int(elapsed)}s] ArgoCD poll failed: {last_status.error_message}")
        else:
            _log_poll_line(last_status, int(elapsed))

            if last_status.sync_status == "Synced" and last_status.health_status == "Healthy":
                last_status.poll_duration_seconds = elapsed
                return last_status   # ← clean exit

            if last_status.health_status == "Degraded":
                last_status.success = False
                last_status.error_message = (
                    "Application entered Degraded health state during sync. "
                    "Check pod logs: kubectl logs -n <ns> -l app.kubernetes.io/instance=<release>"
                )
                last_status.poll_duration_seconds = elapsed
                return last_status   # ← fail fast

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _run_git(args: list[str]) -> None:
    """
    Runs a git subcommand, raising subprocess.CalledProcessError on failure.

    Capturing stdout/stderr prevents git output from leaking into terminal
    output during CI — callers decide what to print via GitOpsResult.
    """
    subprocess.run(
        ["git"] + args,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _get_current_sha() -> str:
    """Returns the short git SHA of HEAD, or 'unknown' on failure."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip() or "unknown"


def _find_chart_dir() -> Path:
    """
    Auto-detects the Helm chart directory relative to the repo root.

    Tries paths in order; returns the first one that exists.
    The fallback path is returned as-is even if it doesn't exist so
    write_image_override() can surface a clear FileNotFoundError
    instead of a confusing AttributeError.
    """
    candidates = [
        Path("k8s/helm/guardops-app"),
        Path("../k8s/helm/guardops-app"),
        Path("../../k8s/helm/guardops-app"),
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return Path("k8s/helm/guardops-app")   # best-effort fallback


def _split_image_ref(image_ref: str) -> tuple[str, str]:
    """
    Splits a full image reference into (repository, tag).

    Handles ECR registry URLs which contain colons in the hostname:
      "123.dkr.ecr.ap-south-1.amazonaws.com/app:abc1234"
      → ("123.dkr.ecr.ap-south-1.amazonaws.com/app", "abc1234")

    Splitting on the LAST colon (rfind) is the correct approach because
    the registry hostname port separator (if present) also uses a colon.
    """
    if ":" in image_ref:
        idx = image_ref.rfind(":")
        return image_ref[:idx], image_ref[idx + 1:]
    return image_ref, "latest"


def _log_poll_line(status: SyncResult, elapsed: int) -> None:
    """Prints a single poll-iteration status line with Rich colour coding."""
    sync_color   = "green" if status.sync_status   == "Synced"  else "yellow"
    health_color = (
        "green"  if status.health_status == "Healthy"   else
        "red"    if status.health_status == "Degraded"  else
        "yellow"
    )
    info(
        f"[{elapsed}s] "
        f"Sync: [{sync_color}]{status.sync_status}[/{sync_color}]  "
        f"Health: [{health_color}]{status.health_status}[/{health_color}]  "
        f"Rev: {status.revision or '—'}"
    )

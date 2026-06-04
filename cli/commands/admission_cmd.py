"""
cli/commands/admission_cmd.py — `guardops admission` (Phase 11 + v1.0.0).

Applies the GuardOps Kyverno admission-control policies (keyless image-signature
verification, SBOM attestation, best-practice pack) in either mode:

  guardops admission                 # Audit (default, safe — reports, never blocks)
  guardops admission --mode enforce  # Enforce (blocks unsigned/non-compliant pods)
  guardops admission --mode enforce --dry-run   # render without applying

Audit is the default so this is safe to run blind. The policy YAMLs in k8s/kyverno
ship with __POLICY_ACTION__ / __DIGEST_PIN__ placeholders (Kyverno forbids
mutateDigest in Audit), which this command substitutes — the same rendering the
PowerShell setup-admission-control.ps1 -Enforce path does, but testable and
cross-platform.
"""

import subprocess
import sys
from pathlib import Path

import click

from cli.utils.output import header, info, success, error, warn, blank

DEFAULT_POLICY_DIR = "k8s/kyverno"
DEFAULT_GITHUB_REPO = "Bihan-Banerjee/GuardOps"
# GitHub Actions OIDC issuer — the keyless cosign identity Kyverno verifies against.
CI_ISSUER = "https://token.actions.githubusercontent.com"


def render_policy(text: str, mode: str, github_repo: str = DEFAULT_GITHUB_REPO) -> str:
    """Substitute every policy placeholder for the chosen mode + CI identity.

    Audit  → validationFailureAction=Audit,  mutateDigest=false (Kyverno forbids
             mutation in Audit).
    Enforce→ validationFailureAction=Enforce, mutateDigest=true (pin the digest).
    __CI_ISSUER__ / __CI_SUBJECT__ → the GitHub Actions OIDC identity that signs the
    images (verify-images.yaml). Mirrors scripts/setup-admission-control.ps1.
    """
    action = "Enforce" if mode == "enforce" else "Audit"
    digest = "true" if mode == "enforce" else "false"
    ci_subject = f"https://github.com/{github_repo}/.github/workflows/ci.yaml@refs/heads/*"
    return (
        text.replace("__POLICY_ACTION__", action)
        .replace("__DIGEST_PIN__", digest)
        .replace("__CI_ISSUER__", CI_ISSUER)
        .replace("__CI_SUBJECT__", ci_subject)
    )


@click.command("admission")
@click.option("--mode", type=click.Choice(["audit", "enforce"], case_sensitive=False),
              default="audit", show_default=True,
              help="Kyverno policy action. audit = report only (safe); enforce = block violations.")
@click.option("--policy-dir", default=None, type=click.Path(file_okay=False),
              help=f"Directory of Kyverno policy YAMLs (default: {DEFAULT_POLICY_DIR}).")
@click.option("--github-repo", default=DEFAULT_GITHUB_REPO, show_default=True,
              help="owner/repo whose GitHub Actions identity signs the images (verify-images).")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Render the policies and print what would apply, without calling kubectl.")
def admission_command(mode, policy_dir, github_repo, dry_run):
    """Apply the GuardOps Kyverno admission policies in Audit (default) or Enforce mode."""
    mode = mode.lower()
    action = "Enforce" if mode == "enforce" else "Audit"

    pdir = Path(policy_dir or DEFAULT_POLICY_DIR)
    if not pdir.is_dir():
        error(f"Policy directory [cyan]{pdir}[/cyan] not found. "
              "Run from the GuardOps repo root, or pass [cyan]--policy-dir[/cyan].")
        sys.exit(1)

    # networkpolicy* files are reference-only egress templates, not ClusterPolicies —
    # skip them, mirroring morning-start.ps1.
    policies = sorted(p for p in pdir.glob("*.yaml") if not p.name.startswith("networkpolicy"))
    if not policies:
        error(f"No .yaml policies found in [cyan]{pdir}[/cyan].")
        sys.exit(1)

    header("GuardOps · Admission Control", f"Kyverno policies — mode: {action}")
    if mode == "enforce":
        warn("Enforce BLOCKS unsigned / non-compliant pods at admission. "
             "Confirm cosign signing works end-to-end first, or deploys will fail.")

    rendered = {
        p.name: render_policy(p.read_text(encoding="utf-8"), mode, github_repo)
        for p in policies
    }

    if dry_run:
        for name in rendered:
            info(f"would apply [cyan]{name}[/cyan]  (validationFailureAction={action})")
        blank()
        success(f"Dry run: {len(rendered)} policy file(s) rendered in {action} mode — nothing applied.")
        return

    applied = 0
    for name, text in rendered.items():
        proc = subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=text, text=True, capture_output=True, encoding="utf-8",
        )
        if proc.returncode != 0:
            error(f"Failed to apply {name}: {(proc.stderr or proc.stdout or '').strip()}")
            sys.exit(1)
        success(f"Applied [cyan]{name}[/cyan]")
        applied += 1

    blank()
    success(f"Applied {applied} Kyverno policy file(s) in [bold]{action}[/bold] mode.")

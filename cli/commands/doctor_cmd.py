"""
cli/commands/doctor_cmd.py — `guardops doctor` (v1.0.0).

A first-run preflight. GuardOps shells out to a dozen external tools (docker, kubectl,
helm, the scanners, cosign/syft, aws/terraform); `doctor` reports everything missing
*at once* — with install hints — instead of failing one subprocess at a time in the
middle of a deploy. It also checks that the project is configured.

Exit code is 1 only when a REQUIRED tool is missing, so it doubles as a CI/onboarding
gate. Recommended/optional tools just warn.
"""

import importlib.util
import shutil
import sys

import click

from cli.utils.output import header, section, success, error, warn, info, blank
from cli.utils.config import config_exists

# (tool, what it's for, install hint)
_CORE = [
    ("docker", "build + run images", "https://docs.docker.com/get-docker/"),
    ("kubectl", "talk to the cluster", "https://kubernetes.io/docs/tasks/tools/"),
    ("helm", "deploy the app", "https://helm.sh/docs/intro/install/"),
]
_SCANNERS = [
    ("semgrep", "SAST", "pip install semgrep"),
    ("bandit", "Python SAST", "pip install bandit"),
    ("trivy", "CVE / secret / IaC scan", "https://trivy.dev/latest/getting-started/installation/"),
]
_SUPPLY_CHAIN = [
    ("git", "GitOps image overrides", "https://git-scm.com/downloads"),
    ("cosign", "sign + verify images", "https://docs.sigstore.dev/cosign/system_config/installation/"),
    ("syft", "SBOM generation", "https://github.com/anchore/syft#installation"),
]
_CLOUD = [
    ("aws", "ECR / S3 / Route53 (prod)", "https://aws.amazon.com/cli/"),
    ("terraform", "provision AWS infra", "https://developer.hashicorp.com/terraform/install"),
]


@click.command("doctor")
def doctor_command():
    """Check that required tools are installed and the project is configured."""
    header("GuardOps · Doctor", "Preflight check for tools and configuration")

    missing_required = _report("Required — core deploy (local/staging/prod)", _CORE, required=True)
    _report("Security scanners — pre-deploy gate", _SCANNERS, required=False)
    _report("Supply chain — sign + SBOM (prod)", _SUPPLY_CHAIN, required=False)
    _report("Cloud — AWS + Terraform (prod only)", _CLOUD, required=False)

    section("Project")
    if config_exists():
        success(".guardops.yaml found")
    else:
        warn("No [cyan].guardops.yaml[/cyan] — run [cyan]guardops init[/cyan] to scaffold one")

    if importlib.util.find_spec("uvicorn") and importlib.util.find_spec("fastapi"):
        success("Dashboard extra installed (fastapi + uvicorn)")
    else:
        info("Dashboard extra not installed — [cyan]pip install 'guardops\\[dashboard]'[/cyan] "
             "to run [cyan]guardops dashboard[/cyan]")
    blank()

    if missing_required:
        plural = "s" if missing_required > 1 else ""
        error(f"{missing_required} required tool{plural} missing — install the above before deploying.")
        sys.exit(1)
    success("All required tools present. You're good to go.")


def _report(title: str, tools: list[tuple[str, str, str]], *, required: bool) -> int:
    """Print one group's status. Returns the count of missing tools when required."""
    section(title)
    missing = 0
    for name, purpose, hint in tools:
        path = shutil.which(name)
        if path:
            success(f"[bold]{name}[/bold] — {purpose}  [dim]{path}[/dim]")
        elif required:
            error(f"[bold]{name}[/bold] — {purpose}\n     install: [cyan]{hint}[/cyan]")
            missing += 1
        else:
            warn(f"[bold]{name}[/bold] missing — {purpose}\n     install: [cyan]{hint}[/cyan]")
    blank()
    return missing

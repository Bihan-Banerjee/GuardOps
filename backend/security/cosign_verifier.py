"""
backend/security/cosign_verifier.py

Phase 11 — cosign keyless signature + attestation verification.

Wraps the `cosign` CLI to verify that an image carries a valid keyless signature
(and optionally a CycloneDX SBOM attestation) from the GuardOps CI workflow
identity. Mirrors the subprocess conventions of the other runners. Powers
`guardops verify-image` — the local proof that the CI signing works end-to-end,
checking the same identity Kyverno enforces at admission on EKS.
"""

import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

DEFAULT_OIDC_ISSUER = "https://token.actions.githubusercontent.com"


def default_identity_regexp(github_repo: str = "Bihan-Banerjee/GuardOps") -> str:
    """Regexp matching the CI workflow's Fulcio certificate identity (any branch)."""
    repo = github_repo.replace(".", r"\.")
    return rf"https://github\.com/{repo}/\.github/workflows/ci\.yaml@.*"


@dataclass
class VerifyResult:
    tool: str
    success: bool                 # cosign ran AND verification passed
    verified: bool = False
    image_ref: str = ""
    kind: str = "signature"       # "signature" | "attestation:<type>"
    output: str = ""
    error_message: str = ""
    skipped: bool = False
    skip_reason: str = ""


def verify_image(
    image_ref: str,
    identity_regexp: Optional[str] = None,
    oidc_issuer: str = DEFAULT_OIDC_ISSUER,
    timeout: int = 120,
) -> VerifyResult:
    """Verify the keyless signature on image_ref against the CI identity."""
    if not shutil.which("cosign"):
        return VerifyResult(
            tool="cosign",
            success=False,
            skipped=True,
            skip_reason="cosign not installed. See https://docs.sigstore.dev/cosign/installation",
            image_ref=image_ref,
        )

    identity_regexp = identity_regexp or default_identity_regexp()
    cmd = [
        "cosign", "verify",
        "--certificate-identity-regexp", identity_regexp,
        "--certificate-oidc-issuer", oidc_issuer,
        "--output", "json",
        image_ref,
    ]
    return _run_cosign(cmd, image_ref, kind="signature", timeout=timeout)


def verify_attestation(
    image_ref: str,
    attestation_type: str = "cyclonedx",
    identity_regexp: Optional[str] = None,
    oidc_issuer: str = DEFAULT_OIDC_ISSUER,
    timeout: int = 120,
) -> VerifyResult:
    """Verify a signed attestation (e.g. the CycloneDX SBOM) on image_ref."""
    if not shutil.which("cosign"):
        return VerifyResult(
            tool="cosign",
            success=False,
            skipped=True,
            skip_reason="cosign not installed",
            image_ref=image_ref,
        )

    identity_regexp = identity_regexp or default_identity_regexp()
    cmd = [
        "cosign", "verify-attestation",
        "--type", attestation_type,
        "--certificate-identity-regexp", identity_regexp,
        "--certificate-oidc-issuer", oidc_issuer,
        image_ref,
    ]
    return _run_cosign(cmd, image_ref, kind=f"attestation:{attestation_type}", timeout=timeout)


def _run_cosign(cmd: list[str], image_ref: str, kind: str, timeout: int) -> VerifyResult:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return VerifyResult(
            tool="cosign",
            success=False,
            image_ref=image_ref,
            kind=kind,
            error_message=f"cosign timed out after {timeout} seconds",
        )

    if result.returncode == 0:
        return VerifyResult(
            tool="cosign",
            success=True,
            verified=True,
            image_ref=image_ref,
            kind=kind,
            output=(result.stdout or result.stderr).strip(),
        )

    return VerifyResult(
        tool="cosign",
        success=False,
        verified=False,
        image_ref=image_ref,
        kind=kind,
        error_message=(result.stderr or result.stdout).strip()[:500],
    )

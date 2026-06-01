"""
cli/commands/verify_cmd.py

`guardops verify-image` — Phase 11 command.

Verifies that an image carries a valid cosign keyless signature (and optionally
the CycloneDX SBOM attestation) from the GuardOps CI workflow identity. This is
the local proof that the CI signing in the container-scan job works, checking the
same identity Kyverno enforces at admission on EKS.

Usage:
  guardops verify-image <image-ref>
  guardops verify-image <image-ref> --attestation
  guardops verify-image <image-ref> --repo owner/name

Exit codes:
  0 — signature (and attestation, if requested) verified
  1 — verification failed, or cosign not installed

Phase 11 — new command. Registered in cli/main.py as "verify-image".
"""

import sys

import click

from cli.utils.output import info, success, error, warn, console
from backend.security.cosign_verifier import (
    verify_image,
    verify_attestation,
    default_identity_regexp,
    DEFAULT_OIDC_ISSUER,
)


@click.command("verify-image")
@click.argument("image_ref")
@click.option(
    "--repo",
    default="Bihan-Banerjee/GuardOps",
    show_default=True,
    help="GitHub owner/name used to build the expected signer identity regexp.",
)
@click.option(
    "--identity-regexp",
    default=None,
    help="Override the certificate identity regexp directly (advanced).",
)
@click.option(
    "--oidc-issuer",
    default=DEFAULT_OIDC_ISSUER,
    show_default=True,
    help="Expected OIDC issuer of the signing identity.",
)
@click.option(
    "--attestation",
    is_flag=True,
    default=False,
    help="Also verify the signed CycloneDX SBOM attestation.",
)
def verify_image_command(image_ref, repo, identity_regexp, oidc_issuer, attestation):
    """
    Verify the cosign keyless signature on an image.

    \b
    Examples:
      guardops verify-image 123.dkr.ecr.ap-south-1.amazonaws.com/guardops-app@sha256:...
      guardops verify-image <ref> --attestation
    """
    identity = identity_regexp or default_identity_regexp(repo)

    console.print()
    console.rule("[bold]GuardOps [cyan]Verify Image[/cyan][/bold]")
    info(f"Image:    [cyan]{image_ref}[/cyan]")
    info(f"Identity: [dim]{identity}[/dim]")
    info(f"Issuer:   [dim]{oidc_issuer}[/dim]")

    res = verify_image(image_ref, identity_regexp=identity, oidc_issuer=oidc_issuer)

    if res.skipped:
        error(res.skip_reason)
        sys.exit(1)

    if not res.success:
        error(f"Signature verification FAILED:\n{res.error_message}")
        _hints()
        sys.exit(1)

    success("Signature verified ✓")

    if attestation:
        att = verify_attestation(
            image_ref,
            attestation_type="cyclonedx",
            identity_regexp=identity,
            oidc_issuer=oidc_issuer,
        )
        if att.skipped:
            warn(att.skip_reason)
        elif att.success:
            success("CycloneDX SBOM attestation verified ✓")
        else:
            error(f"Attestation verification FAILED:\n{att.error_message}")
            sys.exit(1)

    console.print()


def _hints() -> None:
    console.print()
    console.print("  [bold]Hints:[/bold]")
    console.print("    [dim]- The image must be signed by CI (a push to main runs the signing steps).[/dim]")
    console.print("    [dim]- Sign by digest, not a mutable tag: pass ...@sha256:<digest>[/dim]")
    console.print("    [dim]- List signatures/attestations: cosign tree <ref>[/dim]")
    console.print()

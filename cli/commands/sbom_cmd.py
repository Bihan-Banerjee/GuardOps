"""
cli/commands/sbom_cmd.py

`guardops sbom` — Phase 11 command.

Generates a CycloneDX + SPDX SBOM for a built image using Syft — the same SBOM the
CI container-scan job produces and attaches to the image as a cosign attestation.

Usage:
  guardops sbom <image-ref>
  guardops sbom <image-ref> --output-dir security/reports

Exit codes:
  0 — SBOM generated
  1 — syft not installed or generation failed

Phase 11 — new command. Registered in cli/main.py as "sbom".
"""

import sys

import click

from cli.utils.output import info, success, error, console
from backend.security.sbom_runner import run_syft


@click.command("sbom")
@click.argument("image_ref")
@click.option(
    "--output-dir",
    default="security/reports",
    show_default=True,
    help="Directory to write the SBOM files into.",
)
def sbom_command(image_ref, output_dir):
    """
    Generate a CycloneDX + SPDX SBOM for an image.

    \b
    Examples:
      guardops sbom guardops-app:latest
      guardops sbom guardops-app:latest --output-dir security/reports
    """
    console.print()
    console.rule("[bold]GuardOps [cyan]SBOM[/cyan][/bold]")
    info(f"Image: [cyan]{image_ref}[/cyan]")

    res = run_syft(image_ref, output_dir=output_dir)

    if res.skipped:
        error(res.skip_reason)
        sys.exit(1)

    if not res.success:
        error(f"SBOM generation FAILED: {res.error_message}")
        sys.exit(1)

    success(f"SBOM generated — {res.package_count} packages")
    for fmt, path in res.formats.items():
        info(f"  {fmt}: [cyan]{path}[/cyan]")
    console.print()

"""
backend/security/sbom_runner.py

Phase 11 — Software Bill of Materials (SBOM) generation via Syft.

Wraps the `syft` CLI to produce CycloneDX + SPDX JSON SBOMs for a built image,
mirroring the subprocess timeout/encoding conventions of trivy_runner.py. Used by
`guardops sbom` for local parity with the CI container-scan job, which generates
the same SBOM and attaches it to the image as a cosign attestation.
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SbomResult:
    tool: str
    success: bool
    formats: dict[str, str] = field(default_factory=dict)  # syft format -> file path
    package_count: int = 0
    image_ref: str = ""
    error_message: str = ""
    skipped: bool = False
    skip_reason: str = ""


# Syft output format -> default filename (matches the CI container-scan job).
DEFAULT_FORMATS = {
    "cyclonedx-json": "sbom.cdx.json",
    "spdx-json": "sbom.spdx.json",
}


def run_syft(
    image_ref: str,
    output_dir: str = "security/reports",
    formats: Optional[dict[str, str]] = None,
    timeout: int = 300,
) -> SbomResult:
    """
    Generate SBOM(s) for image_ref using Syft.

    formats maps a syft output format (e.g. "cyclonedx-json") to an output
    filename. Defaults to CycloneDX + SPDX JSON — the two formats CI attests.
    """
    if not shutil.which("syft"):
        return SbomResult(
            tool="syft",
            success=False,
            skipped=True,
            skip_reason="syft not installed. See https://github.com/anchore/syft",
            image_ref=image_ref,
        )

    formats = formats or dict(DEFAULT_FORMATS)
    os.makedirs(output_dir, exist_ok=True)

    cmd = ["syft", image_ref]
    out_paths: dict[str, str] = {}
    for fmt, fname in formats.items():
        path = os.path.join(output_dir, fname)
        out_paths[fmt] = path
        cmd += ["-o", f"{fmt}={path}"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return SbomResult(
            tool="syft",
            success=False,
            error_message=f"syft timed out after {timeout} seconds",
            image_ref=image_ref,
        )

    if result.returncode != 0:
        return SbomResult(
            tool="syft",
            success=False,
            error_message=f"syft failed (exit {result.returncode}): {result.stderr[:300]}",
            image_ref=image_ref,
        )

    # Confirm files were actually written; count packages from the CycloneDX SBOM.
    written = {fmt: p for fmt, p in out_paths.items() if os.path.exists(p)}
    if not written:
        return SbomResult(
            tool="syft",
            success=False,
            error_message="syft reported success but no SBOM files were written",
            image_ref=image_ref,
        )

    return SbomResult(
        tool="syft",
        success=True,
        formats=written,
        package_count=_count_packages(written),
        image_ref=image_ref,
    )


def _count_packages(written: dict[str, str]) -> int:
    """Best-effort package count, preferring CycloneDX 'components'."""
    for fmt, path in written.items():
        if "cyclonedx" in fmt:
            try:
                with open(path, encoding="utf-8") as fh:
                    return len(json.load(fh).get("components", []))
            except Exception:
                pass
    for fmt, path in written.items():
        if "spdx" in fmt:
            try:
                with open(path, encoding="utf-8") as fh:
                    return len(json.load(fh).get("packages", []))
            except Exception:
                pass
    return 0

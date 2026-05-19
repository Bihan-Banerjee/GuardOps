"""
backend/security/zap_runner.py

OWASP ZAP DAST (Dynamic Application Security Testing) runner — Phase 6.

Unlike the SAST runners (Semgrep, Bandit, Trivy) which scan source code and
images BEFORE deploy, ZAP scans the LIVE running application AFTER Helm deploy.
This catches a different class of vulnerabilities: runtime misconfigurations,
missing security headers, exposed endpoints, and injection flaws that only
appear when the app is actually serving HTTP traffic.

Scan mode: Baseline (passive) scan only.
  - Spiders the target URL and inspects responses
  - Does NOT send attack payloads (safe for prod)
  - Catches ~40% of OWASP Top 10 without any active probing
  Active scan (zap-full-scan.py) is intentionally excluded — it sends attack
  payloads and is too disruptive for a post-deploy gate. Add it to a scheduled
  nightly job once the baseline is stable.

Severity mapping (ZAP riskcode → GuardOps unified scale):
  3 = High        → CRITICAL  (live runtime findings are one tier higher than SAST)
  2 = Medium      → HIGH
  1 = Low         → MEDIUM
  0 = Informational → LOW

Why bump one tier up from SAST?
  A HIGH SAST finding is a code pattern that *might* be exploitable.
  A HIGH ZAP finding is a confirmed HTTP-level vulnerability on a running service.
  The exploit distance is shorter, so severity is treated as one tier higher.

Auto-rollback: deploy_cmd triggers rollback if ZapScanResult.blocked is True
  (i.e. any CRITICAL finding). Controlled by `security.zap_fail_on` in config.

Requires:
  - Docker installed and running (uses owasp/zap2docker-stable image)
  - Target URL reachable from the machine running the scan
  - Output dir writable (reports written there as zap-report.json + zap-report.html)
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class ZapFinding:
    """Single alert raised by ZAP, normalised to the GuardOps severity scale."""
    severity: str           # CRITICAL | HIGH | MEDIUM | LOW
    alert: str              # Short name, e.g. "Missing Anti-clickjacking Header"
    description: str        # Full description from ZAP
    solution: str           # Recommended fix
    url: str                # First affected URL
    evidence: str           # Raw evidence snippet (may be empty)
    confidence: str         # Low | Medium | High | Confirmed
    zap_riskcode: int       # Original ZAP riskcode (0-3) for reference
    instance_count: int     # Number of affected URLs/parameters


@dataclass
class ZapScanResult:
    """
    Return type of run_zap_baseline(). Mirrors the interface of the SAST runner
    results so deploy_cmd can handle them consistently.

    Intentionally NOT fed into generate_report() — ZAP is post-deploy and gets
    its own section in the result_panel rather than the pre-deploy scan table.
    """
    tool: str = "owasp-zap"
    success: bool = False
    skipped: bool = False
    skip_reason: str = ""
    error_message: str = ""

    findings: list[ZapFinding] = field(default_factory=list)
    blocked: bool = False           # True if CRITICAL+ findings exist
    fail_on_severity: str = "CRITICAL"

    # Report file paths (absolute)
    json_report_path: str = ""
    html_report_path: str = ""

    scan_duration_seconds: float = 0.0

    @property
    def severity_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts


# ── Severity Mapping ──────────────────────────────────────────────────────────

_ZAP_RISKCODE_TO_SEVERITY: dict[int, str] = {
    3: "CRITICAL",
    2: "HIGH",
    1: "MEDIUM",
    0: "LOW",
}

_SEVERITY_ORDER: dict[str, int] = {
    "LOW": 0,
    "MEDIUM": 1,
    "HIGH": 2,
    "CRITICAL": 3,
}


def _severity_gte(sev: str, threshold: str) -> bool:
    """Return True if `sev` is >= `threshold` on the severity scale."""
    return _SEVERITY_ORDER.get(sev, 0) >= _SEVERITY_ORDER.get(threshold, 3)


# ── Main Entry Point ──────────────────────────────────────────────────────────

def run_zap_baseline(
    target_url: str,
    config: dict[str, Any],
    output_dir: str = "security/reports",
) -> ZapScanResult:
    """
    Run OWASP ZAP baseline (passive) scan against `target_url`.

    Args:
        target_url:  Full URL of the live application, e.g. "http://localhost:8080"
        config:      GuardOps config dict (from load_config())
        output_dir:  Directory to write zap-report.json and zap-report.html

    Returns:
        ZapScanResult with findings, severity counts, and blocked flag.

    The scan runs ZAP inside Docker (owasp/zap2docker-stable). The container
    mounts `output_dir` at /zap/wrk and writes reports there.
    ZAP exit codes are intentionally ignored (-I flag) — we gate on the parsed
    JSON ourselves so the GuardOps severity threshold is the single source of truth.
    """
    result = ZapScanResult()
    start = time.time()

    security_cfg = config.get("security", {})
    tools_cfg = security_cfg.get("tools", {})

    # ── Skip check ────────────────────────────────────────────────────────────
    if not tools_cfg.get("owasp_zap", False):
        result.skipped = True
        result.skip_reason = (
            "owasp_zap disabled in .guardops.yaml "
            "(set security.tools.owasp_zap: true to enable)"
        )
        return result

    if not target_url:
        result.skipped = True
        result.skip_reason = (
            "No target URL provided. Set security.zap_target_url in .guardops.yaml "
            "or pass the service URL from deploy_result.service_url."
        )
        return result

    fail_on = security_cfg.get("zap_fail_on", "CRITICAL").upper()
    result.fail_on_severity = fail_on

    # ── Prepare output directory ──────────────────────────────────────────────
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    json_report = out_path / "zap-report.json"
    html_report = out_path / "zap-report.html"
    result.json_report_path = str(json_report.resolve())
    result.html_report_path = str(html_report.resolve())

    # ── Build Docker command ──────────────────────────────────────────────────
    #
    # owasp/zap2docker-stable runs zap-baseline.py which:
    #   1. Starts ZAP daemon
    #   2. Spiders the target (follows links, fills forms passively)
    #   3. Runs passive scan rules on all captured traffic
    #   4. Writes JSON + HTML reports
    #   5. Exits (we use -I to suppress non-zero exit on findings)
    #
    # Flags:
    #   -t  target URL
    #   -J  JSON report filename (relative to /zap/wrk)
    #   -r  HTML report filename (relative to /zap/wrk)
    #   -I  ignore warn-level findings for exit code (we parse JSON ourselves)
    #   -l WARN  include WARN and above in reports (excludes INFO noise)
    #   --auto   use the packaged automation framework config

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{out_path.resolve()}:/zap/wrk:rw",
        "ghcr.io/zaproxy/zaproxy:stable",
        "zap-baseline.py",
        "-t", target_url,
        "-J", "zap-report.json",
        "-r", "zap-report.html",
        "-I",                            # don't fail on warnings
        "-l", "WARN",                    # include WARN+ alerts
    ]

    timeout_seconds = config.get("security", {}).get("zap_timeout_seconds", 300)

    # ── Run ZAP ───────────────────────────────────────────────────────────────
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
        # ZAP exits 0 (no alerts), 1 (warn-level alerts), 2 (fail-level alerts).
        # With -I flag: 0 or 1 are both "scan completed". 2 = internal ZAP error.
        if proc.returncode == 2:
            result.error_message = (
                f"ZAP encountered an internal error (exit code 2).\n"
                f"stderr: {proc.stderr[:500]}"
            )
            return result

    except subprocess.TimeoutExpired:
        result.error_message = (
            f"ZAP scan timed out after {timeout_seconds}s. "
            "Increase security.zap_timeout_seconds in .guardops.yaml "
            "or check that the target URL is reachable."
        )
        return result

    except FileNotFoundError:
        result.error_message = (
            "Docker not found. ZAP runs inside Docker — ensure Docker Desktop "
            "is running and `docker` is on your PATH."
        )
        return result

    # ── Parse JSON report ─────────────────────────────────────────────────────
    if not json_report.exists():
        result.error_message = (
            f"ZAP JSON report not found at {json_report}. "
            "The scan may have failed before writing output. "
            f"ZAP stderr: {proc.stderr[:300]}"
        )
        return result

    try:
        raw = json_report.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        result.error_message = f"Failed to parse ZAP JSON report: {exc}"
        return result

    # ── Normalise findings ────────────────────────────────────────────────────
    findings: list[ZapFinding] = []

    for site in data.get("site", []):
        for alert in site.get("alerts", []):
            riskcode = int(alert.get("riskcode", 0))
            severity = _ZAP_RISKCODE_TO_SEVERITY.get(riskcode, "LOW")

            instances = alert.get("instances", [{}])
            first_instance = instances[0] if instances else {}

            findings.append(ZapFinding(
                severity=severity,
                alert=alert.get("alert", "Unknown"),
                description=_strip_html(alert.get("desc", "")),
                solution=_strip_html(alert.get("solution", "")),
                url=first_instance.get("uri", site.get("@name", target_url)),
                evidence=first_instance.get("evidence", ""),
                confidence=alert.get("confidence", ""),
                zap_riskcode=riskcode,
                instance_count=len(instances),
            ))

    result.findings = findings
    result.success = True
    result.scan_duration_seconds = time.time() - start

    # ── Determine blocked ─────────────────────────────────────────────────────
    result.blocked = any(
        _severity_gte(f.severity, fail_on) for f in findings
    )

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    """
    Very lightweight HTML tag stripper for ZAP description fields.
    ZAP wraps desc/solution in <p> tags. We just want plain text for terminal output.
    Does NOT use a proper HTML parser on purpose — keeps this module dependency-free.
    """
    import re
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

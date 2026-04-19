import json
import shutil
import subprocess
from backend.security.semgrep_runner import ScanResult, SecurityFinding
from typing import Optional

SEVERITY_MAP = {
    "CRITICAL": "CRITICAL",
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "LOW": "LOW",
    "UNKNOWN": "LOW",
}


def run_trivy_image(
    image_ref: str,
    config: dict,
    severities: Optional[list[str]] = None,
) -> ScanResult:
    if not shutil.which("trivy"):
        return ScanResult(
            tool="trivy",
            success=False,
            skipped=True,
            skip_reason="trivy not installed. See https://aquasecurity.github.io/trivy",
        )

    severities = severities or ["CRITICAL", "HIGH", "MEDIUM"]
    severity_str = ",".join(severities)

    cmd = [
        "trivy", "image",
        "--format", "json",
        "--quiet",
        "--severity", severity_str,
        "--no-progress",
        image_ref,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return ScanResult(
            tool="trivy",
            success=False,
            error_message="Trivy timed out after 300 seconds",
        )

    if not result.stdout.strip():
        return ScanResult(
            tool="trivy",
            success=False,
            error_message=f"Trivy produced no output. stderr: {result.stderr[:300]}",
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ScanResult(
            tool="trivy",
            success=False,
            error_message=f"Could not parse trivy output: {result.stdout[:300]}",
        )

    findings = []
    for target in data.get("Results", []):
        target_path = target.get("Target", "")
        for vuln in target.get("Vulnerabilities") or []:
            severity = SEVERITY_MAP.get(vuln.get("Severity", "UNKNOWN"), "LOW")
            pkg = vuln.get("PkgName", "")
            installed = vuln.get("InstalledVersion", "")
            fixed = vuln.get("FixedVersion", "")
            fix_guidance = f"Upgrade {pkg} from {installed} to {fixed}" if fixed else f"No fix available for {pkg}@{installed}"

            findings.append(SecurityFinding(
                tool="trivy",
                rule_id=vuln.get("VulnerabilityID", "unknown"),
                severity=severity,
                message=vuln.get("Title") or vuln.get("Description", "No description")[:120],
                file_path=target_path,
                line_start=0,
                line_end=0,
                cve=vuln.get("VulnerabilityID", ""),
                fix_guidance=fix_guidance,
            ))

    return ScanResult(
        tool="trivy",
        success=True,
        findings=findings,
    )


def run_trivy_filesystem(
    target_path: str,
    config: dict,
) -> ScanResult:
    if not shutil.which("trivy"):
        return ScanResult(
            tool="trivy-fs",
            success=False,
            skipped=True,
            skip_reason="trivy not installed",
        )

    cmd = [
        "trivy", "fs",
        "--format", "json",
        "--quiet",
        "--security-checks", "secret,config",
        "--no-progress",
        target_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return ScanResult(tool="trivy-fs", success=False, error_message="Timeout")

    if not result.stdout.strip():
        return ScanResult(tool="trivy-fs", success=True, findings=[])

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ScanResult(tool="trivy-fs", success=True, findings=[])

    findings = []
    for target in data.get("Results", []):
        for secret in target.get("Secrets") or []:
            findings.append(SecurityFinding(
                tool="trivy-fs",
                rule_id=secret.get("RuleID", "secret"),
                severity="HIGH",
                message=f"Secret detected: {secret.get('Title', 'unknown')}",
                file_path=target.get("Target", ""),
                line_start=secret.get("StartLine", 0),
                line_end=secret.get("EndLine", 0),
            ))
        for misconfig in target.get("Misconfigurations") or []:
            severity = SEVERITY_MAP.get(misconfig.get("Severity", "LOW"), "LOW")
            findings.append(SecurityFinding(
                tool="trivy-fs",
                rule_id=misconfig.get("ID", "misconfig"),
                severity=severity,
                message=misconfig.get("Message", ""),
                file_path=target.get("Target", ""),
                line_start=0,
                line_end=0,
                fix_guidance=misconfig.get("Resolution", ""),
            ))

    return ScanResult(tool="trivy-fs", success=True, findings=findings)

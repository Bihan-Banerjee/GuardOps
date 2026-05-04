import json
import shutil
import subprocess
from backend.security.semgrep_runner import ScanResult, SecurityFinding

SEVERITY_MAP = {
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "LOW": "LOW",
}

CONFIDENCE_WEIGHT = {
    "HIGH": 1,
    "MEDIUM": 0,
    "LOW": -1,
}


def _adjust_severity(severity: str, confidence: str) -> str:
    order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    base = order.index(SEVERITY_MAP.get(severity, "LOW"))
    weight = CONFIDENCE_WEIGHT.get(confidence, 0)
    adjusted = max(0, min(len(order) - 1, base + weight))
    return order[adjusted]


def run_bandit(
    target_path: str,
    config: dict,
    severity_level: str = "l",
) -> ScanResult:
    if not shutil.which("bandit"):
        return ScanResult(
            tool="bandit",
            success=False,
            skipped=True,
            skip_reason="bandit not installed. Run: pip install bandit",
        )

    cmd = [
        "bandit",
        "-r", target_path,
        "--exclude", ".venv,tests,infra,k8s,security",
        "-f", "json",
        "-q",
        f"-{severity_level}",
        "--exclude", ".venv,venv,tests,node_modules",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return ScanResult(
            tool="bandit",
            success=False,
            error_message="Bandit timed out after 120 seconds",
        )

    raw = result.stdout.strip() or result.stderr.strip()
    if not raw:
        return ScanResult(tool="bandit", success=True, findings=[])

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ScanResult(
            tool="bandit",
            success=False,
            error_message=f"Could not parse bandit output: {raw[:300]}",
        )

    findings = []
    for issue in data.get("results", []):
        severity = _adjust_severity(
            issue.get("issue_severity", "LOW"),
            issue.get("issue_confidence", "MEDIUM"),
        )
        findings.append(SecurityFinding(
            tool="bandit",
            rule_id=issue.get("test_id", "unknown"),
            severity=severity,
            message=issue.get("issue_text", ""),
            file_path=issue.get("filename", ""),
            line_start=issue.get("line_number", 0),
            line_end=issue.get("line_range", [0])[-1],
            code_snippet=issue.get("code", ""),
            fix_guidance=issue.get("more_info", ""),
        ))

    return ScanResult(
        tool="bandit",
        success=True,
        findings=findings,
    )
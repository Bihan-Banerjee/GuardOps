import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SecurityFinding:
    tool: str
    rule_id: str
    severity: str
    message: str
    file_path: str
    line_start: int
    line_end: int
    code_snippet: str = ""
    cve: str = ""
    fix_guidance: str = ""


@dataclass
class ScanResult:
    tool: str
    success: bool
    findings: list[SecurityFinding] = field(default_factory=list)
    error_message: str = ""
    skipped: bool = False
    skip_reason: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "CRITICAL")

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "HIGH")

    @property
    def medium_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "MEDIUM")

    @property
    def low_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "LOW")

    def has_blocking_findings(self, fail_on: str = "HIGH") -> bool:
        severity_order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
        threshold_index = severity_order.index(fail_on.upper())
        return any(
            severity_order.index(f.severity) >= threshold_index
            for f in self.findings
        )


SEVERITY_MAP = {
    "ERROR": "HIGH",
    "WARNING": "MEDIUM",
    "INFO": "LOW",
    "error": "HIGH",
    "warning": "MEDIUM",
    "info": "LOW",
}


def run_semgrep(
    target_path: str,
    config: dict,
    extra_configs: Optional[list[str]] = None,
) -> ScanResult:
    if not shutil.which("semgrep"):
        return ScanResult(
            tool="semgrep",
            success=False,
            skipped=True,
            skip_reason="semgrep not installed. Run: pip install semgrep",
        )

    configs = extra_configs or ["p/python", "p/security-audit", "p/secrets"]
    cmd = ["semgrep", "scan", "--json", "--quiet"]
    for cfg in configs:
        cmd += ["--config", cfg]
    cmd.append(target_path)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return ScanResult(
            tool="semgrep",
            success=False,
            error_message="Semgrep timed out after 300 seconds",
        )

    raw_output = result.stdout.strip()
    if not raw_output:
        raw_output = result.stderr.strip()

    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError:
        if result.returncode == 0:
            return ScanResult(tool="semgrep", success=True, findings=[])
        return ScanResult(
            tool="semgrep",
            success=False,
            error_message=f"Could not parse semgrep output: {raw_output[:300]}",
        )

    findings = []
    for r in data.get("results", []):
        raw_severity = r.get("extra", {}).get("severity", "INFO")
        severity = SEVERITY_MAP.get(raw_severity, "LOW")
        metadata = r.get("extra", {}).get("metadata", {})

        findings.append(SecurityFinding(
            tool="semgrep",
            rule_id=r.get("check_id", "unknown"),
            severity=severity,
            message=r.get("extra", {}).get("message", "No message"),
            file_path=r.get("path", ""),
            line_start=r.get("start", {}).get("line", 0),
            line_end=r.get("end", {}).get("line", 0),
            code_snippet=r.get("extra", {}).get("lines", ""),
            cve=metadata.get("cve", ""),
            fix_guidance=metadata.get("fix", ""),
        ))

    errors = data.get("errors", [])
    error_msg = ""
    if errors:
        error_msg = f"{len(errors)} scan error(s): {errors[0].get('message', '')}"

    return ScanResult(
        tool="semgrep",
        success=True,
        findings=findings,
        error_message=error_msg,
    )
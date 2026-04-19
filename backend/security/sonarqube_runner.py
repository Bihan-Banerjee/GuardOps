import os
import shutil
import subprocess
import time
import requests
from dataclasses import dataclass
from typing import Optional
from backend.security.semgrep_runner import ScanResult, SecurityFinding

SONAR_SEVERITY_MAP = {
    "BLOCKER": "CRITICAL",
    "CRITICAL": "HIGH",
    "MAJOR": "MEDIUM",
    "MINOR": "LOW",
    "INFO": "LOW",
}


@dataclass
class SonarQubeConfig:
    host_url: str
    token: str
    project_key: str
    project_name: str
    source_path: str = "."


def run_sonarqube(
    target_path: str,
    config: dict,
) -> ScanResult:
    sonar_cfg = _build_sonar_config(target_path, config)
    if sonar_cfg is None:
        return ScanResult(
            tool="sonarqube",
            success=False,
            skipped=True,
            skip_reason=(
                "SonarQube skipped: SONAR_TOKEN or SONAR_HOST_URL not set. "
                "Add them to your .env file to enable SonarQube analysis."
            ),
        )

    scan_result = _run_sonar_scanner(sonar_cfg)
    if not scan_result:
        return ScanResult(
            tool="sonarqube",
            success=False,
            error_message="sonar-scanner failed. Check sonar-scanner is installed and SONAR_HOST_URL is reachable.",
        )

    task_success = _wait_for_analysis(sonar_cfg)
    if not task_success:
        return ScanResult(
            tool="sonarqube",
            success=False,
            error_message="SonarQube analysis task did not complete in time.",
        )

    findings = _fetch_issues(sonar_cfg)
    return ScanResult(
        tool="sonarqube",
        success=True,
        findings=findings,
    )


def _build_sonar_config(target_path: str, config: dict) -> Optional[SonarQubeConfig]:
    token = os.environ.get("SONAR_TOKEN", "")
    host_url = os.environ.get("SONAR_HOST_URL", "")

    if not token or not host_url:
        return None

    project_name = config.get("project", {}).get("name", "guardops-project")
    project_key = project_name.replace("-", "_").replace(" ", "_").lower()

    return SonarQubeConfig(
        host_url=host_url.rstrip("/"),
        token=token,
        project_key=project_key,
        project_name=project_name,
        source_path=target_path,
    )


def _run_sonar_scanner(cfg: SonarQubeConfig) -> bool:
    scanner = shutil.which("sonar-scanner") or shutil.which("sonar-scanner.bat")
    if not scanner:
        subprocess_result = _try_mvn_sonar(cfg)
        return subprocess_result

    cmd = [
        scanner,
        f"-Dsonar.projectKey={cfg.project_key}",
        f"-Dsonar.projectName={cfg.project_name}",
        f"-Dsonar.sources={cfg.source_path}",
        f"-Dsonar.host.url={cfg.host_url}",
        f"-Dsonar.token={cfg.token}",
        "-Dsonar.scm.disabled=true",
        "-Dsonar.sourceEncoding=UTF-8",
        f"-Dsonar.exclusions=**/.venv/**,**/node_modules/**,**/tests/**",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return result.returncode == 0


def _try_mvn_sonar(cfg: SonarQubeConfig) -> bool:
    if not shutil.which("mvn"):
        return False
    cmd = [
        "mvn", "sonar:sonar",
        f"-Dsonar.projectKey={cfg.project_key}",
        f"-Dsonar.host.url={cfg.host_url}",
        f"-Dsonar.token={cfg.token}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return result.returncode == 0


def _wait_for_analysis(cfg: SonarQubeConfig, timeout_seconds: int = 120) -> bool:
    url = f"{cfg.host_url}/api/ce/component"
    params = {"component": cfg.project_key}
    headers = {"Authorization": f"Bearer {cfg.token}"}

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                current = data.get("current", {})
                status = current.get("status", "")
                if status == "SUCCESS":
                    return True
                if status in ("FAILED", "CANCELED"):
                    return False
        except requests.RequestException:
            pass
        time.sleep(5)
    return False


def _fetch_issues(cfg: SonarQubeConfig) -> list[SecurityFinding]:
    url = f"{cfg.host_url}/api/issues/search"
    headers = {"Authorization": f"Bearer {cfg.token}"}
    params = {
        "componentKeys": cfg.project_key,
        "statuses": "OPEN,REOPENED",
        "types": "VULNERABILITY,BUG,CODE_SMELL",
        "ps": 500,
    }

    findings = []
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code != 200:
            return findings

        data = resp.json()
        for issue in data.get("issues", []):
            raw_severity = issue.get("severity", "INFO")
            severity = SONAR_SEVERITY_MAP.get(raw_severity, "LOW")
            component = issue.get("component", "")
            file_path = component.split(":")[-1] if ":" in component else component
            text_range = issue.get("textRange", {})

            findings.append(SecurityFinding(
                tool="sonarqube",
                rule_id=issue.get("rule", "unknown"),
                severity=severity,
                message=issue.get("message", ""),
                file_path=file_path,
                line_start=text_range.get("startLine", 0),
                line_end=text_range.get("endLine", 0),
                fix_guidance=f"Type: {issue.get('type', '')} | Effort: {issue.get('effort', 'unknown')}",
            ))
    except requests.RequestException:
        pass

    return findings


def check_quality_gate(config: dict) -> tuple[bool, str]:
    sonar_cfg = _build_sonar_config(".", config)
    if sonar_cfg is None:
        return True, "SonarQube not configured — quality gate skipped"

    url = f"{sonar_cfg.host_url}/api/qualitygates/project_status"
    params = {"projectKey": sonar_cfg.project_key}
    headers = {"Authorization": f"Bearer {sonar_cfg.token}"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return True, f"Could not fetch quality gate status (HTTP {resp.status_code})"

        data = resp.json()
        status = data.get("projectStatus", {}).get("status", "")
        if status == "OK":
            return True, "Quality gate passed"
        elif status == "ERROR":
            failed_conditions = [
                c for c in data.get("projectStatus", {}).get("conditions", [])
                if c.get("status") == "ERROR"
            ]
            reasons = [c.get("metricKey", "") for c in failed_conditions]
            return False, f"Quality gate FAILED on: {', '.join(reasons)}"
        return True, f"Quality gate status: {status}"
    except requests.RequestException as e:
        return True, f"Could not reach SonarQube: {e}"
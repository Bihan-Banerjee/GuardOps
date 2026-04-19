import pytest
from unittest.mock import MagicMock, patch
from backend.security.semgrep_runner import (
    run_semgrep, ScanResult, SecurityFinding, SEVERITY_MAP
)
from backend.security.bandit_runner import run_bandit, _adjust_severity
from backend.security.report_generator import generate_report, ConsolidatedReport


# ── Semgrep tests ──────────────────────────────────────────────────────────────

def test_semgrep_skipped_when_not_installed(mocker):
    mocker.patch("backend.security.semgrep_runner.shutil.which", return_value=None)
    result = run_semgrep(".", {})
    assert result.skipped is True
    assert result.tool == "semgrep"


def test_semgrep_returns_findings(mocker):
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = '{"results": [{"check_id": "python.lang.security.audit.eval-detected", "path": "app.py", "start": {"line": 10}, "end": {"line": 10}, "extra": {"severity": "ERROR", "message": "Use of eval() detected", "lines": "eval(user_input)", "metadata": {}}}], "errors": []}'
    mock_proc.stderr = ""
    mocker.patch("backend.security.semgrep_runner.subprocess.run", return_value=mock_proc)

    result = run_semgrep(".", {})
    assert result.success is True
    assert len(result.findings) == 1
    assert result.findings[0].severity == "HIGH"
    assert result.findings[0].rule_id == "python.lang.security.audit.eval-detected"


def test_semgrep_clean_scan(mocker):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = '{"results": [], "errors": []}'
    mock_proc.stderr = ""
    mocker.patch("backend.security.semgrep_runner.subprocess.run", return_value=mock_proc)
    mocker.patch("backend.security.semgrep_runner.shutil.which", return_value="/usr/bin/semgrep")

    result = run_semgrep(".", {})
    assert result.success is True
    assert len(result.findings) == 0
    assert result.has_blocking_findings("HIGH") is False


def test_semgrep_timeout(mocker):
    import subprocess
    mocker.patch("backend.security.semgrep_runner.shutil.which", return_value="/usr/bin/semgrep")
    mocker.patch(
        "backend.security.semgrep_runner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="semgrep", timeout=300)
    )
    result = run_semgrep(".", {})
    assert result.success is False
    assert "timed out" in result.error_message


# ── Bandit tests ───────────────────────────────────────────────────────────────

def test_bandit_skipped_when_not_installed(mocker):
    mocker.patch("backend.security.bandit_runner.shutil.which", return_value=None)
    result = run_bandit(".", {})
    assert result.skipped is True


def test_adjust_severity_high_confidence_raises():
    assert _adjust_severity("MEDIUM", "HIGH") == "HIGH"


def test_adjust_severity_low_confidence_lowers():
    assert _adjust_severity("HIGH", "LOW") == "MEDIUM"


def test_adjust_severity_stays_in_bounds():
    assert _adjust_severity("LOW", "LOW") == "LOW"
    assert _adjust_severity("HIGH", "HIGH") == "CRITICAL"


def test_bandit_parses_findings(mocker):
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = json_bandit_output()
    mock_proc.stderr = ""
    mocker.patch("backend.security.bandit_runner.shutil.which", return_value="/usr/bin/bandit")
    mocker.patch("backend.security.bandit_runner.subprocess.run", return_value=mock_proc)

    result = run_bandit(".", {})
    assert result.success is True
    assert len(result.findings) >= 1


def json_bandit_output() -> str:
    import json
    return json.dumps({
        "results": [{
            "test_id": "B102",
            "test_name": "exec_used",
            "issue_text": "Use of exec detected.",
            "issue_severity": "MEDIUM",
            "issue_confidence": "HIGH",
            "filename": "app.py",
            "line_number": 5,
            "line_range": [5, 5],
            "code": "exec(user_cmd)",
            "more_info": "https://bandit.readthedocs.io/en/latest/plugins/b102_exec_used.html",
        }],
        "metrics": {"_totals": {"SEVERITY.HIGH": 0, "SEVERITY.MEDIUM": 1}},
    })


# ── ScanResult tests ───────────────────────────────────────────────────────────

def test_scan_result_blocking_detection():
    result = ScanResult(
        tool="test",
        success=True,
        findings=[
            SecurityFinding("test", "r1", "HIGH", "msg", "file.py", 1, 1),
            SecurityFinding("test", "r2", "LOW",  "msg", "file.py", 2, 2),
        ]
    )
    assert result.has_blocking_findings("HIGH") is True
    assert result.has_blocking_findings("CRITICAL") is False
    assert result.high_count == 1
    assert result.low_count == 1


def test_scan_result_not_blocking_when_only_medium():
    result = ScanResult(
        tool="test",
        success=True,
        findings=[
            SecurityFinding("test", "r1", "MEDIUM", "msg", "file.py", 1, 1),
        ]
    )
    assert result.has_blocking_findings("HIGH") is False
    assert result.has_blocking_findings("MEDIUM") is True


# ── Report generator tests ─────────────────────────────────────────────────────

def test_generate_report_creates_files(tmp_path):
    scan_results = [
        ScanResult(
            tool="semgrep",
            success=True,
            findings=[
                SecurityFinding("semgrep", "rule1", "HIGH", "Test finding", "app.py", 10, 10)
            ]
        ),
        ScanResult(tool="bandit", success=True, skipped=True, skip_reason="not installed"),
    ]

    report = generate_report(
        scan_results=scan_results,
        project_name="test-project",
        image_ref="test-project:abc123",
        output_dir=str(tmp_path),
        fail_on_severity="HIGH",
    )

    assert report.blocked is True
    assert (tmp_path / "latest.json").exists()
    assert (tmp_path / "latest.html").exists()
    assert report.severity_counts["HIGH"] == 1
    assert report.severity_counts["CRITICAL"] == 0


def test_generate_report_not_blocked_when_only_medium(tmp_path):
    scan_results = [
        ScanResult(
            tool="semgrep",
            success=True,
            findings=[
                SecurityFinding("semgrep", "r1", "MEDIUM", "msg", "f.py", 1, 1),
                SecurityFinding("semgrep", "r2", "LOW",    "msg", "f.py", 2, 2),
            ]
        )
    ]
    report = generate_report(
        scan_results=scan_results,
        project_name="myapp",
        image_ref="myapp:latest",
        output_dir=str(tmp_path),
        fail_on_severity="HIGH",
    )
    assert report.blocked is False


def test_consolidated_report_to_dict():
    report = ConsolidatedReport(
        project_name="myapp",
        image_ref="myapp:latest",
        timestamp="2024-01-01T00:00:00",
        scan_results=[ScanResult(tool="semgrep", success=True, findings=[])],
    )
    d = report.to_dict()
    assert d["project_name"] == "myapp"
    assert d["blocked"] is False
    assert isinstance(d["tools_run"], list)
    assert isinstance(d["findings"], list)
"""
tests/test_scan_cmd.py — `guardops scan`.

Covers the pass/blocked panels, the --image container scan, --no-report (in-memory
report), the SonarQube quality gate, and the per-tool result rendering (findings /
error / skipped). All scanners + report writing are mocked.
"""

from types import SimpleNamespace

from cli.commands.scan_cmd import scan_command
from backend.security.semgrep_runner import ScanResult, SecurityFinding

_P = "cli.commands.scan_cmd."


def _finding(sev="HIGH"):
    return SecurityFinding(tool="semgrep", rule_id="R1", severity=sev, message="bad pattern",
                           file_path="app.py", line_start=3, line_end=3)


def _scan(tool="semgrep", success=True, findings=None, skipped=False, skip_reason="", error_message=""):
    return ScanResult(tool=tool, success=success, findings=findings or [],
                      skipped=skipped, skip_reason=skip_reason, error_message=error_message)


def _passing_report():
    return SimpleNamespace(blocked=False,
                           severity_counts={"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0})


def _wire(monkeypatch, **over):
    s = monkeypatch.setattr
    s(_P + "load_config", lambda: {"security": {}})
    s(_P + "get_project_name", lambda c: "demo")
    s(_P + "_git_short_sha", lambda: "abc1234")
    s(_P + "run_semgrep", lambda *a: over.get("semgrep", _scan("semgrep")))
    s(_P + "run_bandit", lambda *a: over.get("bandit", _scan("bandit")))
    s(_P + "run_trivy_filesystem", lambda *a: over.get("trivy_fs", _scan("trivy-fs")))
    s(_P + "run_trivy_image", lambda *a: over.get("trivy_img", _scan("trivy")))
    s(_P + "run_sonarqube",
      lambda *a: over.get("sonar", _scan("sonarqube", success=False, skipped=True, skip_reason="no server")))
    s(_P + "check_quality_gate", lambda c: (True, "passed"))
    s(_P + "generate_report", lambda **k: over.get("report", _passing_report()))
    s(_P + "persist_report_safe", lambda *a, **k: None)


def test_scan_passed(runner, monkeypatch):
    _wire(monkeypatch)
    r = runner.invoke(scan_command, [])
    assert r.exit_code == 0 and "Scan PASSED" in r.output


def test_scan_blocked_exits_one(runner, monkeypatch):
    _wire(monkeypatch, report=SimpleNamespace(
        blocked=True, severity_counts={"CRITICAL": 1, "HIGH": 0, "MEDIUM": 0, "LOW": 0}))
    r = runner.invoke(scan_command, [])
    assert r.exit_code == 1 and "Scan FAILED" in r.output


def test_scan_with_image_runs_container_scan(runner, monkeypatch):
    _wire(monkeypatch, trivy_img=_scan("trivy", findings=[_finding("HIGH")]))
    r = runner.invoke(scan_command, ["--image", "myapp:latest"])
    assert r.exit_code == 0
    assert "trivy" in r.output.lower()


def test_scan_no_report_builds_in_memory(runner, monkeypatch):
    _wire(monkeypatch)
    r = runner.invoke(scan_command, ["--no-report"])
    assert r.exit_code == 0   # real ConsolidatedReport built from (empty) scan results


def test_scan_renders_findings_and_errors(runner, monkeypatch):
    _wire(monkeypatch,
          semgrep=_scan("semgrep", findings=[_finding("CRITICAL"), _finding("HIGH"),
                                             _finding("MEDIUM"), _finding("LOW"),
                                             _finding("HIGH"), _finding("LOW")]),
          bandit=_scan("bandit", success=False, error_message="bandit crashed"))
    r = runner.invoke(scan_command, [])
    assert r.exit_code == 0
    assert "critical" in r.output.lower()
    assert "and 1 more" in r.output            # >5 findings truncated


def test_scan_sonarqube_quality_gate_failure(runner, monkeypatch):
    _wire(monkeypatch, sonar=_scan("sonarqube", success=True, findings=[]))
    monkeypatch.setattr(_P + "check_quality_gate", lambda c: (False, "gate failed"))
    r = runner.invoke(scan_command, [])
    assert "Quality gate" in r.output

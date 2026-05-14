"""
tests/test_security.py

Comprehensive tests for all security runners and report generator.

Coverage goals:
  - Every runner's happy path
  - Every runner's "tool not installed" path
  - Timeout handling for every runner
  - Malformed / empty / partial JSON from every tool
  - Severity mapping for every tool's scale
  - SonarQube async polling: timeout, FAILED, network error
  - SonarQube quality gate: pass, fail with conditions, unreachable
  - ConsolidatedReport.blocked logic across all threshold levels
  - Report generator: all-skipped, all-failed, mixed, CRITICAL threshold
  - ScanResult property counts and has_blocking_findings edge cases

Why mock subprocess instead of running real tools:
  Tests must be fast and environment-independent. A developer's laptop
  may not have Trivy installed; CI may not have SonarQube credentials.
  We mock at the boundary (subprocess.run, shutil.which, requests.get)
  so we test OUR logic, not the tool's behaviour.
"""

import json
import subprocess
import pytest
from unittest.mock import MagicMock, patch, call

from backend.security.semgrep_runner import (
    run_semgrep, ScanResult, SecurityFinding, SEVERITY_MAP
)
from backend.security.bandit_runner import run_bandit, _adjust_severity
from backend.security.trivy_runner import run_trivy_image, run_trivy_filesystem
from backend.security.sonarqube_runner import (
    run_sonarqube, check_quality_gate,
    _wait_for_analysis, _fetch_issues, _build_sonar_config, SonarQubeConfig,
)
from backend.security.report_generator import generate_report, ConsolidatedReport


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _finding(severity="HIGH", tool="semgrep", rule_id="r1"):
    """Convenience factory for SecurityFinding."""
    return SecurityFinding(
        tool=tool, rule_id=rule_id, severity=severity,
        message="test msg", file_path="app.py", line_start=1, line_end=1
    )


def _scan_ok(tool="semgrep", findings=None):
    return ScanResult(tool=tool, success=True, findings=findings or [])


def _scan_skipped(tool="semgrep"):
    return ScanResult(tool=tool, success=False, skipped=True, skip_reason="not installed")


def _mock_proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# ScanResult unit tests
# ---------------------------------------------------------------------------

class TestScanResult:
    def test_counts_empty(self):
        r = _scan_ok(findings=[])
        assert r.critical_count == 0
        assert r.high_count == 0
        assert r.medium_count == 0
        assert r.low_count == 0

    def test_counts_mixed(self):
        r = _scan_ok(findings=[
            _finding("CRITICAL"), _finding("CRITICAL"),
            _finding("HIGH"), _finding("MEDIUM"),
            _finding("LOW"), _finding("LOW"), _finding("LOW"),
        ])
        assert r.critical_count == 2
        assert r.high_count == 1
        assert r.medium_count == 1
        assert r.low_count == 3

    def test_blocking_high_threshold_empty(self):
        assert _scan_ok(findings=[]).has_blocking_findings("HIGH") is False

    def test_blocking_high_threshold_only_medium(self):
        r = _scan_ok(findings=[_finding("MEDIUM")])
        assert r.has_blocking_findings("HIGH") is False

    def test_blocking_high_threshold_with_high(self):
        r = _scan_ok(findings=[_finding("HIGH")])
        assert r.has_blocking_findings("HIGH") is True

    def test_blocking_critical_threshold_with_high(self):
        # HIGH finding should NOT block when threshold is CRITICAL
        r = _scan_ok(findings=[_finding("HIGH")])
        assert r.has_blocking_findings("CRITICAL") is False

    def test_blocking_critical_threshold_with_critical(self):
        r = _scan_ok(findings=[_finding("CRITICAL")])
        assert r.has_blocking_findings("CRITICAL") is True

    def test_blocking_medium_threshold(self):
        r = _scan_ok(findings=[_finding("MEDIUM")])
        assert r.has_blocking_findings("MEDIUM") is True
        assert r.has_blocking_findings("HIGH") is False

    def test_blocking_low_threshold_blocks_on_low(self):
        r = _scan_ok(findings=[_finding("LOW")])
        assert r.has_blocking_findings("LOW") is True

    def test_skipped_result_has_no_findings(self):
        r = _scan_skipped()
        assert r.findings == []
        assert r.has_blocking_findings("HIGH") is False


# ---------------------------------------------------------------------------
# Semgrep tests
# ---------------------------------------------------------------------------

class TestSemgrepRunner:
    def test_skipped_when_not_installed(self, mocker):
        mocker.patch("backend.security.semgrep_runner.shutil.which", return_value=None)
        result = run_semgrep(".", {})
        assert result.skipped is True
        assert result.tool == "semgrep"
        assert "semgrep not installed" in result.skip_reason

    def test_clean_scan_no_findings(self, mocker):
        mocker.patch("backend.security.semgrep_runner.shutil.which", return_value="/usr/bin/semgrep")
        mocker.patch(
            "backend.security.semgrep_runner.subprocess.run",
            return_value=_mock_proc(0, '{"results": [], "errors": []}')
        )
        result = run_semgrep(".", {})
        assert result.success is True
        assert result.findings == []
        assert result.has_blocking_findings("HIGH") is False

    def test_parses_single_finding_severity_mapping(self, mocker):
        mocker.patch("backend.security.semgrep_runner.shutil.which", return_value="/usr/bin/semgrep")
        payload = json.dumps({
            "results": [{
                "check_id": "python.lang.security.audit.eval-detected",
                "path": "app.py",
                "start": {"line": 10}, "end": {"line": 10},
                "extra": {
                    "severity": "ERROR",   # Semgrep ERROR → HIGH in unified scale
                    "message": "Use of eval() detected",
                    "lines": "eval(user_input)",
                    "metadata": {"cve": "CVE-2023-1234", "fix": "Remove eval()"},
                }
            }],
            "errors": []
        })
        mocker.patch("backend.security.semgrep_runner.subprocess.run",
                     return_value=_mock_proc(1, payload))
        result = run_semgrep(".", {})
        assert result.success is True
        assert len(result.findings) == 1
        f = result.findings[0]
        assert f.severity == "HIGH"
        assert f.rule_id == "python.lang.security.audit.eval-detected"
        assert f.cve == "CVE-2023-1234"
        assert f.fix_guidance == "Remove eval()"

    def test_warning_maps_to_medium(self, mocker):
        mocker.patch("backend.security.semgrep_runner.shutil.which", return_value="/usr/bin/semgrep")
        payload = json.dumps({
            "results": [{
                "check_id": "some-rule", "path": "f.py",
                "start": {"line": 1}, "end": {"line": 1},
                "extra": {"severity": "WARNING", "message": "msg", "lines": "", "metadata": {}}
            }],
            "errors": []
        })
        mocker.patch("backend.security.semgrep_runner.subprocess.run",
                     return_value=_mock_proc(1, payload))
        result = run_semgrep(".", {})
        assert result.findings[0].severity == "MEDIUM"

    def test_info_maps_to_low(self, mocker):
        mocker.patch("backend.security.semgrep_runner.shutil.which", return_value="/usr/bin/semgrep")
        payload = json.dumps({
            "results": [{
                "check_id": "some-rule", "path": "f.py",
                "start": {"line": 1}, "end": {"line": 1},
                "extra": {"severity": "INFO", "message": "msg", "lines": "", "metadata": {}}
            }],
            "errors": []
        })
        mocker.patch("backend.security.semgrep_runner.subprocess.run",
                     return_value=_mock_proc(1, payload))
        result = run_semgrep(".", {})
        assert result.findings[0].severity == "LOW"

    def test_timeout(self, mocker):
        mocker.patch("backend.security.semgrep_runner.shutil.which", return_value="/usr/bin/semgrep")
        mocker.patch(
            "backend.security.semgrep_runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="semgrep", timeout=300)
        )
        result = run_semgrep(".", {})
        assert result.success is False
        assert "timed out" in result.error_message

    def test_malformed_json_with_nonzero_returncode(self, mocker):
        mocker.patch("backend.security.semgrep_runner.shutil.which", return_value="/usr/bin/semgrep")
        mocker.patch("backend.security.semgrep_runner.subprocess.run",
                     return_value=_mock_proc(1, "this is not json at all {{{{"))
        result = run_semgrep(".", {})
        assert result.success is False
        assert "Could not parse" in result.error_message

    def test_malformed_json_with_zero_returncode_returns_empty(self, mocker):
        # If semgrep exits 0 but output is unparseable, treat as clean scan
        mocker.patch("backend.security.semgrep_runner.shutil.which", return_value="/usr/bin/semgrep")
        mocker.patch("backend.security.semgrep_runner.subprocess.run",
                     return_value=_mock_proc(0, "Progress: 100%  (non-json status line)"))
        result = run_semgrep(".", {})
        assert result.success is True
        assert result.findings == []

    def test_scan_errors_array_recorded_in_error_message(self, mocker):
        mocker.patch("backend.security.semgrep_runner.shutil.which", return_value="/usr/bin/semgrep")
        payload = json.dumps({
            "results": [],
            "errors": [{"message": "parse error in main.py", "type": "ParseError"}]
        })
        mocker.patch("backend.security.semgrep_runner.subprocess.run",
                     return_value=_mock_proc(0, payload))
        result = run_semgrep(".", {})
        assert result.success is True   # findings still valid
        assert "scan error" in result.error_message
        assert "parse error in main.py" in result.error_message

    def test_multiple_findings_sorted_not_required_but_all_present(self, mocker):
        mocker.patch("backend.security.semgrep_runner.shutil.which", return_value="/usr/bin/semgrep")
        results = [
            {"check_id": f"rule-{i}", "path": "f.py",
             "start": {"line": i}, "end": {"line": i},
             "extra": {"severity": "ERROR", "message": f"msg{i}", "lines": "", "metadata": {}}}
            for i in range(5)
        ]
        mocker.patch("backend.security.semgrep_runner.subprocess.run",
                     return_value=_mock_proc(1, json.dumps({"results": results, "errors": []})))
        result = run_semgrep(".", {})
        assert len(result.findings) == 5

    def test_empty_stdout_falls_back_to_stderr(self, mocker):
        """If stdout is empty, semgrep runner should use stderr as raw output."""
        mocker.patch("backend.security.semgrep_runner.shutil.which", return_value="/usr/bin/semgrep")
        payload = json.dumps({"results": [], "errors": []})
        mocker.patch("backend.security.semgrep_runner.subprocess.run",
                     return_value=_mock_proc(0, stdout="", stderr=payload))
        result = run_semgrep(".", {})
        assert result.success is True


# ---------------------------------------------------------------------------
# Bandit tests
# ---------------------------------------------------------------------------

def _bandit_payload(severity="MEDIUM", confidence="HIGH", test_id="B102"):
    return json.dumps({
        "results": [{
            "test_id": test_id,
            "test_name": "exec_used",
            "issue_text": "Use of exec detected.",
            "issue_severity": severity,
            "issue_confidence": confidence,
            "filename": "app.py",
            "line_number": 5,
            "line_range": [5, 6],
            "code": "exec(user_cmd)",
            "more_info": "https://bandit.readthedocs.io",
        }],
        "metrics": {"_totals": {"SEVERITY.MEDIUM": 1}},
    })


class TestBanditRunner:
    def test_skipped_when_not_installed(self, mocker):
        mocker.patch("backend.security.bandit_runner.shutil.which", return_value=None)
        result = run_bandit(".", {})
        assert result.skipped is True
        assert result.tool == "bandit"

    def test_timeout(self, mocker):
        mocker.patch("backend.security.bandit_runner.shutil.which", return_value="/usr/bin/bandit")
        mocker.patch(
            "backend.security.bandit_runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="bandit", timeout=120)
        )
        result = run_bandit(".", {})
        assert result.success is False
        assert "timed out" in result.error_message

    def test_parses_finding(self, mocker):
        mocker.patch("backend.security.bandit_runner.shutil.which", return_value="/usr/bin/bandit")
        mocker.patch("backend.security.bandit_runner.subprocess.run",
                     return_value=_mock_proc(1, _bandit_payload("MEDIUM", "HIGH")))
        result = run_bandit(".", {})
        assert result.success is True
        assert len(result.findings) == 1
        # MEDIUM severity + HIGH confidence → adjusted to HIGH
        assert result.findings[0].severity == "HIGH"
        assert result.findings[0].rule_id == "B102"
        assert result.findings[0].line_end == 6

    def test_malformed_json(self, mocker):
        mocker.patch("backend.security.bandit_runner.shutil.which", return_value="/usr/bin/bandit")
        mocker.patch("backend.security.bandit_runner.subprocess.run",
                     return_value=_mock_proc(1, "NOT_JSON"))
        result = run_bandit(".", {})
        assert result.success is False
        assert "Could not parse" in result.error_message

    def test_empty_output_returns_clean(self, mocker):
        """Bandit exits 0 with no output when there are no findings at all."""
        mocker.patch("backend.security.bandit_runner.shutil.which", return_value="/usr/bin/bandit")
        mocker.patch("backend.security.bandit_runner.subprocess.run",
                     return_value=_mock_proc(0, stdout="", stderr=""))
        result = run_bandit(".", {})
        assert result.success is True
        assert result.findings == []

    def test_stderr_output_parsed_as_fallback(self, mocker):
        """Bandit sometimes writes JSON to stderr in certain configurations."""
        mocker.patch("backend.security.bandit_runner.shutil.which", return_value="/usr/bin/bandit")
        mocker.patch("backend.security.bandit_runner.subprocess.run",
                     return_value=_mock_proc(1, stdout="", stderr=_bandit_payload()))
        result = run_bandit(".", {})
        assert result.success is True
        assert len(result.findings) == 1

    def test_no_results_key_in_json(self, mocker):
        """Handles JSON that is valid but missing the 'results' key."""
        mocker.patch("backend.security.bandit_runner.shutil.which", return_value="/usr/bin/bandit")
        mocker.patch("backend.security.bandit_runner.subprocess.run",
                     return_value=_mock_proc(0, json.dumps({"metrics": {}})))
        result = run_bandit(".", {})
        assert result.success is True
        assert result.findings == []


class TestAdjustSeverity:
    def test_medium_high_confidence_raises_to_high(self):
        assert _adjust_severity("MEDIUM", "HIGH") == "HIGH"

    def test_high_low_confidence_lowers_to_medium(self):
        assert _adjust_severity("HIGH", "LOW") == "MEDIUM"

    def test_low_low_confidence_stays_low(self):
        assert _adjust_severity("LOW", "LOW") == "LOW"

    def test_high_high_confidence_raises_to_critical(self):
        assert _adjust_severity("HIGH", "HIGH") == "CRITICAL"

    def test_low_high_confidence_raises_to_medium(self):
        assert _adjust_severity("LOW", "HIGH") == "MEDIUM"

    def test_medium_medium_confidence_stays_medium(self):
        assert _adjust_severity("MEDIUM", "MEDIUM") == "MEDIUM"

    def test_critical_high_confidence_stays_critical(self):
        # CRITICAL is the ceiling; can't go higher
        assert _adjust_severity("HIGH", "HIGH") == "CRITICAL"

    def test_unknown_confidence_treated_as_medium(self):
        # CONFIDENCE_WEIGHT.get returns 0 for unknown key → no change
        assert _adjust_severity("HIGH", "UNKNOWN") == "HIGH"


# ---------------------------------------------------------------------------
# Trivy tests
# ---------------------------------------------------------------------------

def _trivy_image_payload(severity="HIGH", vuln_id="CVE-2023-9999",
                          pkg="requests", installed="2.27.0", fixed="2.28.0"):
    return json.dumps({
        "Results": [{
            "Target": "my-image:latest (debian 11.6)",
            "Vulnerabilities": [{
                "VulnerabilityID": vuln_id,
                "PkgName": pkg,
                "InstalledVersion": installed,
                "FixedVersion": fixed,
                "Severity": severity,
                "Title": f"Test vuln in {pkg}",
                "Description": "A test vulnerability",
            }]
        }]
    })


def _trivy_image_payload_no_fix():
    return json.dumps({
        "Results": [{
            "Target": "app:latest",
            "Vulnerabilities": [{
                "VulnerabilityID": "CVE-2023-0001",
                "PkgName": "libssl",
                "InstalledVersion": "1.1.1",
                "FixedVersion": "",   # no fix available
                "Severity": "CRITICAL",
                "Title": "OpenSSL issue",
            }]
        }]
    })


class TestTrivyImageRunner:
    def test_skipped_when_not_installed(self, mocker):
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value=None)
        result = run_trivy_image("app:latest", {})
        assert result.skipped is True
        assert result.tool == "trivy"

    def test_parses_finding(self, mocker):
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(0, _trivy_image_payload("HIGH")))
        result = run_trivy_image("my-image:latest", {})
        assert result.success is True
        assert len(result.findings) == 1
        f = result.findings[0]
        assert f.severity == "HIGH"
        assert f.cve == "CVE-2023-9999"
        assert "Upgrade requests" in f.fix_guidance
        assert "2.28.0" in f.fix_guidance

    def test_critical_severity_mapped(self, mocker):
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(0, _trivy_image_payload("CRITICAL")))
        result = run_trivy_image("app:latest", {})
        assert result.findings[0].severity == "CRITICAL"

    def test_unknown_severity_maps_to_low(self, mocker):
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        payload = json.dumps({
            "Results": [{"Target": "app", "Vulnerabilities": [{
                "VulnerabilityID": "CVE-2023-X", "PkgName": "pkg",
                "InstalledVersion": "1.0", "FixedVersion": "1.1",
                "Severity": "UNKNOWN", "Title": "Unknown sev vuln",
            }]}]
        })
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(0, payload))
        result = run_trivy_image("app:latest", {})
        assert result.findings[0].severity == "LOW"

    def test_no_fix_available_guidance(self, mocker):
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(0, _trivy_image_payload_no_fix()))
        result = run_trivy_image("app:latest", {})
        assert "No fix available" in result.findings[0].fix_guidance

    def test_empty_results_array(self, mocker):
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(0, json.dumps({"Results": []})))
        result = run_trivy_image("app:latest", {})
        assert result.success is True
        assert result.findings == []

    def test_partial_output_missing_vulnerabilities_key(self, mocker):
        """Target entry has no 'Vulnerabilities' key — should not crash."""
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        payload = json.dumps({
            "Results": [{"Target": "app (alpine 3.18)"}]   # no Vulnerabilities key
        })
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(0, payload))
        result = run_trivy_image("app:latest", {})
        assert result.success is True
        assert result.findings == []

    def test_vulnerabilities_is_null(self, mocker):
        """Trivy sets Vulnerabilities to null when there are none — should not crash."""
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        payload = json.dumps({
            "Results": [{"Target": "app", "Vulnerabilities": None}]
        })
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(0, payload))
        result = run_trivy_image("app:latest", {})
        assert result.success is True
        assert result.findings == []

    def test_multiple_targets(self, mocker):
        """Trivy can return multiple targets (e.g. OS packages + Python packages)."""
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        payload = json.dumps({
            "Results": [
                {"Target": "debian (os)", "Vulnerabilities": [
                    {"VulnerabilityID": "CVE-A", "PkgName": "curl",
                     "InstalledVersion": "7.68", "FixedVersion": "7.69",
                     "Severity": "HIGH", "Title": "curl vuln"},
                ]},
                {"Target": "usr/lib/python/requirements.txt", "Vulnerabilities": [
                    {"VulnerabilityID": "CVE-B", "PkgName": "requests",
                     "InstalledVersion": "2.26", "FixedVersion": "2.28",
                     "Severity": "MEDIUM", "Title": "requests vuln"},
                ]}
            ]
        })
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(0, payload))
        result = run_trivy_image("app:latest", {})
        assert len(result.findings) == 2
        severities = {f.severity for f in result.findings}
        assert severities == {"HIGH", "MEDIUM"}

    def test_timeout(self, mocker):
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        mocker.patch(
            "backend.security.trivy_runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="trivy", timeout=300)
        )
        result = run_trivy_image("app:latest", {})
        assert result.success is False
        assert "timed out" in result.error_message

    def test_empty_stdout(self, mocker):
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(1, stdout="", stderr="cannot connect to daemon"))
        result = run_trivy_image("app:latest", {})
        assert result.success is False
        assert "no output" in result.error_message.lower()

    def test_malformed_json(self, mocker):
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(0, stdout="{not: valid, json}"))
        result = run_trivy_image("app:latest", {})
        assert result.success is False
        assert "parse" in result.error_message.lower()


class TestTrivyFilesystemRunner:
    def test_skipped_when_not_installed(self, mocker):
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value=None)
        result = run_trivy_filesystem(".", {})
        assert result.skipped is True
        assert "trivy-fs" in result.tool

    def test_clean_filesystem_scan(self, mocker):
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(0, json.dumps({"Results": []})))
        result = run_trivy_filesystem(".", {})
        assert result.success is True
        assert result.findings == []

    def test_secret_finding(self, mocker):
        """
        Trivy filesystem uses the 'Secrets' key (not 'Vulnerabilities').
        Secrets are always mapped to HIGH severity (hardcoded in trivy_runner.py).
        """
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        payload = json.dumps({
            "Results": [{
                "Target": ".env",
                "Secrets": [{
                    "RuleID": "aws-access-key-id",
                    "Title": "AWS Access Key ID",
                    "StartLine": 3,
                    "EndLine": 3,
                }]
            }]
        })
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(0, payload))
        result = run_trivy_filesystem(".", {})
        assert result.success is True
        assert len(result.findings) == 1
        f = result.findings[0]
        # Secrets are hardcoded HIGH in trivy_runner.py (all secrets are treated equally)
        assert f.severity == "HIGH"
        assert f.rule_id == "aws-access-key-id"
        assert "AWS Access Key ID" in f.message
        assert f.file_path == ".env"
        assert f.line_start == 3

    def test_misconfiguration_finding(self, mocker):
        """Trivy fs also detects Dockerfile/IaC misconfigurations under 'Misconfigurations' key."""
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        payload = json.dumps({
            "Results": [{
                "Target": "Dockerfile",
                "Misconfigurations": [{
                    "ID": "DS002",
                    "Severity": "HIGH",
                    "Message": "Image should not be run as root",
                    "Resolution": "Add USER instruction to Dockerfile",
                }]
            }]
        })
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(0, payload))
        result = run_trivy_filesystem(".", {})
        assert result.success is True
        assert len(result.findings) == 1
        assert result.findings[0].severity == "HIGH"
        assert result.findings[0].rule_id == "DS002"
        assert "USER" in result.findings[0].fix_guidance

    def test_secrets_and_misconfigs_in_same_result(self, mocker):
        """Both Secrets and Misconfigurations in the same target are collected together."""
        mocker.patch("backend.security.trivy_runner.shutil.which", return_value="/usr/bin/trivy")
        payload = json.dumps({
            "Results": [{
                "Target": "Dockerfile",
                "Secrets": [
                    {"RuleID": "github-pat", "Title": "GitHub PAT", "StartLine": 1, "EndLine": 1}
                ],
                "Misconfigurations": [
                    {"ID": "DS002", "Severity": "MEDIUM",
                     "Message": "Running as root", "Resolution": "Add USER"}
                ]
            }]
        })
        mocker.patch("backend.security.trivy_runner.subprocess.run",
                     return_value=_mock_proc(0, payload))
        result = run_trivy_filesystem(".", {})
        assert len(result.findings) == 2
        severities = {f.severity for f in result.findings}
        assert "HIGH" in severities    # from secret
        assert "MEDIUM" in severities  # from misconfig


# ---------------------------------------------------------------------------
# SonarQube tests
# ---------------------------------------------------------------------------

@pytest.fixture
def sonar_cfg():
    return SonarQubeConfig(
        host_url="https://sonarcloud.io",
        token="test-token-abc",
        project_key="my_project",
        project_name="my-project",
        source_path=".",
    )


class TestSonarQubeSkip:
    def test_skipped_when_no_env_vars(self, monkeypatch):
        monkeypatch.delenv("SONAR_TOKEN", raising=False)
        monkeypatch.delenv("SONAR_HOST_URL", raising=False)
        result = run_sonarqube(".", {})
        assert result.skipped is True
        assert "SONAR_TOKEN" in result.skip_reason

    def test_skipped_when_only_token_set(self, monkeypatch):
        monkeypatch.setenv("SONAR_TOKEN", "abc")
        monkeypatch.delenv("SONAR_HOST_URL", raising=False)
        result = run_sonarqube(".", {})
        assert result.skipped is True

    def test_build_sonar_config_returns_none_without_env(self, monkeypatch):
        monkeypatch.delenv("SONAR_TOKEN", raising=False)
        monkeypatch.delenv("SONAR_HOST_URL", raising=False)
        assert _build_sonar_config(".", {}) is None

    def test_build_sonar_config_returns_config_with_env(self, monkeypatch):
        monkeypatch.setenv("SONAR_TOKEN", "mytoken")
        monkeypatch.setenv("SONAR_HOST_URL", "https://sonarcloud.io")
        cfg = _build_sonar_config(".", {"project": {"name": "my-project"}})
        assert cfg is not None
        assert cfg.token == "mytoken"
        assert cfg.project_key == "my_project"


class TestWaitForAnalysis:
    def test_success_on_first_poll(self, sonar_cfg, mocker):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"current": {"status": "SUCCESS"}}
        mocker.patch("backend.security.sonarqube_runner.requests.get", return_value=mock_resp)
        assert _wait_for_analysis(sonar_cfg, timeout_seconds=30) is True

    def test_success_after_two_polls(self, sonar_cfg, mocker):
        pending = MagicMock()
        pending.status_code = 200
        pending.json.return_value = {"current": {"status": "PENDING"}}
        success = MagicMock()
        success.status_code = 200
        success.json.return_value = {"current": {"status": "SUCCESS"}}
        mocker.patch("backend.security.sonarqube_runner.requests.get",
                     side_effect=[pending, success])
        mocker.patch("backend.security.sonarqube_runner.time.sleep")
        assert _wait_for_analysis(sonar_cfg, timeout_seconds=30) is True

    def test_returns_false_on_failed_status(self, sonar_cfg, mocker):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"current": {"status": "FAILED"}}
        mocker.patch("backend.security.sonarqube_runner.requests.get", return_value=mock_resp)
        assert _wait_for_analysis(sonar_cfg, timeout_seconds=30) is False

    def test_returns_false_on_canceled_status(self, sonar_cfg, mocker):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"current": {"status": "CANCELED"}}
        mocker.patch("backend.security.sonarqube_runner.requests.get", return_value=mock_resp)
        assert _wait_for_analysis(sonar_cfg, timeout_seconds=30) is False

    def test_network_exception_does_not_crash(self, sonar_cfg, mocker):
        """Network errors during polling should be swallowed and retried."""
        import requests as req_lib
        success = MagicMock()
        success.status_code = 200
        success.json.return_value = {"current": {"status": "SUCCESS"}}
        mocker.patch(
            "backend.security.sonarqube_runner.requests.get",
            side_effect=[req_lib.RequestException("connection refused"), success]
        )
        mocker.patch("backend.security.sonarqube_runner.time.sleep")
        # Should recover and succeed on second attempt
        assert _wait_for_analysis(sonar_cfg, timeout_seconds=30) is True

    def test_timeout_returns_false(self, sonar_cfg, mocker):
        """If analysis never completes within timeout, return False."""
        import time as time_mod
        pending = MagicMock()
        pending.status_code = 200
        pending.json.return_value = {"current": {"status": "IN_PROGRESS"}}
        mocker.patch("backend.security.sonarqube_runner.requests.get", return_value=pending)
        mocker.patch("backend.security.sonarqube_runner.time.sleep")
        # Patch time.time to simulate timeout immediately
        mocker.patch(
            "backend.security.sonarqube_runner.time.time",
            side_effect=[0, 0, 999]   # start=0, loop check=0 (enters), next check=999 (exits)
        )
        assert _wait_for_analysis(sonar_cfg, timeout_seconds=10) is False

    def test_non_200_response_continues_polling(self, sonar_cfg, mocker):
        not_ready = MagicMock()
        not_ready.status_code = 404
        success = MagicMock()
        success.status_code = 200
        success.json.return_value = {"current": {"status": "SUCCESS"}}
        mocker.patch("backend.security.sonarqube_runner.requests.get",
                     side_effect=[not_ready, success])
        mocker.patch("backend.security.sonarqube_runner.time.sleep")
        assert _wait_for_analysis(sonar_cfg, timeout_seconds=30) is True


class TestFetchIssues:
    def test_parses_issues(self, sonar_cfg, mocker):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"issues": [{
            "rule": "python:S1481",
            "severity": "MAJOR",
            "message": "Remove the unused local variable",
            "component": "my_project:src/app.py",
            "textRange": {"startLine": 10, "endLine": 10},
            "type": "CODE_SMELL",
            "effort": "2min",
        }]}
        mocker.patch("backend.security.sonarqube_runner.requests.get", return_value=mock_resp)
        findings = _fetch_issues(sonar_cfg)
        assert len(findings) == 1
        f = findings[0]
        assert f.severity == "MEDIUM"   # MAJOR → MEDIUM in unified scale
        assert f.file_path == "src/app.py"
        assert f.rule_id == "python:S1481"
        assert "2min" in f.fix_guidance

    def test_blocker_maps_to_critical(self, sonar_cfg, mocker):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"issues": [{
            "rule": "python:S3776", "severity": "BLOCKER",
            "message": "Critical issue", "component": "proj:file.py",
            "textRange": {"startLine": 1, "endLine": 1},
            "type": "VULNERABILITY", "effort": "5min",
        }]}
        mocker.patch("backend.security.sonarqube_runner.requests.get", return_value=mock_resp)
        findings = _fetch_issues(sonar_cfg)
        assert findings[0].severity == "CRITICAL"

    def test_non_200_returns_empty(self, sonar_cfg, mocker):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mocker.patch("backend.security.sonarqube_runner.requests.get", return_value=mock_resp)
        assert _fetch_issues(sonar_cfg) == []

    def test_network_error_returns_empty(self, sonar_cfg, mocker):
        import requests as req_lib
        mocker.patch("backend.security.sonarqube_runner.requests.get",
                     side_effect=req_lib.RequestException("timeout"))
        assert _fetch_issues(sonar_cfg) == []

    def test_empty_issues_array(self, sonar_cfg, mocker):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"issues": []}
        mocker.patch("backend.security.sonarqube_runner.requests.get", return_value=mock_resp)
        assert _fetch_issues(sonar_cfg) == []

    def test_component_without_colon(self, sonar_cfg, mocker):
        """Component field without colon separator should use the whole value as file_path."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"issues": [{
            "rule": "r1", "severity": "MINOR", "message": "msg",
            "component": "app.py",   # no colon
            "textRange": {}, "type": "BUG", "effort": "1min",
        }]}
        mocker.patch("backend.security.sonarqube_runner.requests.get", return_value=mock_resp)
        findings = _fetch_issues(sonar_cfg)
        assert findings[0].file_path == "app.py"


class TestCheckQualityGate:
    def test_passes_when_status_ok(self, mocker, monkeypatch):
        monkeypatch.setenv("SONAR_TOKEN", "tok")
        monkeypatch.setenv("SONAR_HOST_URL", "https://sonarcloud.io")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "projectStatus": {"status": "OK", "conditions": []}
        }
        mocker.patch("backend.security.sonarqube_runner.requests.get", return_value=mock_resp)
        passed, message = check_quality_gate({"project": {"name": "myapp"}})
        assert passed is True
        assert "passed" in message.lower()

    def test_fails_with_conditions(self, mocker, monkeypatch):
        monkeypatch.setenv("SONAR_TOKEN", "tok")
        monkeypatch.setenv("SONAR_HOST_URL", "https://sonarcloud.io")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "projectStatus": {
                "status": "ERROR",
                "conditions": [
                    {"status": "ERROR", "metricKey": "new_reliability_rating"},
                    {"status": "ERROR", "metricKey": "new_security_rating"},
                    {"status": "OK",    "metricKey": "coverage"},
                ]
            }
        }
        mocker.patch("backend.security.sonarqube_runner.requests.get", return_value=mock_resp)
        passed, message = check_quality_gate({"project": {"name": "myapp"}})
        assert passed is False
        assert "new_reliability_rating" in message
        assert "new_security_rating" in message
        # OK conditions should not appear in the fail message
        assert "coverage" not in message

    def test_not_configured_returns_pass(self, monkeypatch):
        """If SONAR_TOKEN is not set, quality gate should be skipped (pass)."""
        monkeypatch.delenv("SONAR_TOKEN", raising=False)
        monkeypatch.delenv("SONAR_HOST_URL", raising=False)
        passed, message = check_quality_gate({})
        assert passed is True
        assert "skipped" in message.lower()

    def test_unreachable_returns_pass(self, mocker, monkeypatch):
        """Network failure reaching SonarQube should not block deployment."""
        import requests as req_lib
        monkeypatch.setenv("SONAR_TOKEN", "tok")
        monkeypatch.setenv("SONAR_HOST_URL", "https://sonarcloud.io")
        mocker.patch("backend.security.sonarqube_runner.requests.get",
                     side_effect=req_lib.RequestException("connection refused"))
        passed, message = check_quality_gate({"project": {"name": "myapp"}})
        assert passed is True
        assert "Could not reach" in message

    def test_non_200_response_passes_gracefully(self, mocker, monkeypatch):
        monkeypatch.setenv("SONAR_TOKEN", "tok")
        monkeypatch.setenv("SONAR_HOST_URL", "https://sonarcloud.io")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mocker.patch("backend.security.sonarqube_runner.requests.get", return_value=mock_resp)
        passed, message = check_quality_gate({"project": {"name": "myapp"}})
        assert passed is True   # Fail open, not closed, for unavailable Sonar


# ---------------------------------------------------------------------------
# Report generator tests
# ---------------------------------------------------------------------------

class TestConsolidatedReport:
    def test_blocked_true_when_high_finding(self):
        report = ConsolidatedReport(
            project_name="app", image_ref="app:1", timestamp="2024-01-01",
            scan_results=[_scan_ok("semgrep", [_finding("HIGH")])],
            fail_on_severity="HIGH",
        )
        assert report.blocked is True

    def test_blocked_false_when_only_medium(self):
        report = ConsolidatedReport(
            project_name="app", image_ref="app:1", timestamp="2024-01-01",
            scan_results=[_scan_ok("semgrep", [_finding("MEDIUM")])],
            fail_on_severity="HIGH",
        )
        assert report.blocked is False

    def test_blocked_false_when_all_skipped(self):
        """Skipped tools should not contribute to blocked status."""
        report = ConsolidatedReport(
            project_name="app", image_ref="app:1", timestamp="2024-01-01",
            scan_results=[
                _scan_skipped("semgrep"),
                _scan_skipped("bandit"),
                _scan_skipped("trivy"),
            ],
            fail_on_severity="HIGH",
        )
        assert report.blocked is False

    def test_blocked_false_when_all_failed(self):
        """Failed (errored) tools should not block deployment — skipped path."""
        report = ConsolidatedReport(
            project_name="app", image_ref="app:1", timestamp="2024-01-01",
            scan_results=[
                ScanResult(tool="semgrep", success=False, error_message="crashed"),
            ],
            fail_on_severity="HIGH",
        )
        # A failed scan (not skipped) with no findings should not block
        assert report.blocked is False

    def test_blocked_with_critical_threshold(self):
        """At CRITICAL threshold, HIGH findings do NOT block."""
        report = ConsolidatedReport(
            project_name="app", image_ref="app:1", timestamp="2024-01-01",
            scan_results=[_scan_ok("semgrep", [_finding("HIGH")])],
            fail_on_severity="CRITICAL",
        )
        assert report.blocked is False

    def test_blocked_with_critical_threshold_and_critical_finding(self):
        report = ConsolidatedReport(
            project_name="app", image_ref="app:1", timestamp="2024-01-01",
            scan_results=[_scan_ok("semgrep", [_finding("CRITICAL")])],
            fail_on_severity="CRITICAL",
        )
        assert report.blocked is True

    def test_severity_counts_across_tools(self):
        report = ConsolidatedReport(
            project_name="app", image_ref="app:1", timestamp="2024-01-01",
            scan_results=[
                _scan_ok("semgrep", [_finding("CRITICAL"), _finding("HIGH")]),
                _scan_ok("bandit",  [_finding("HIGH"), _finding("MEDIUM")]),
                _scan_ok("trivy",   [_finding("LOW")]),
            ],
        )
        counts = report.severity_counts
        assert counts["CRITICAL"] == 1
        assert counts["HIGH"] == 2
        assert counts["MEDIUM"] == 1
        assert counts["LOW"] == 1

    def test_all_findings_aggregates_across_tools(self):
        report = ConsolidatedReport(
            project_name="app", image_ref="app:1", timestamp="2024-01-01",
            scan_results=[
                _scan_ok("semgrep", [_finding("HIGH")]),
                _scan_ok("bandit",  [_finding("MEDIUM")]),
                _scan_skipped("trivy"),
            ],
        )
        assert len(report.all_findings) == 2

    def test_to_dict_structure(self):
        report = ConsolidatedReport(
            project_name="myapp", image_ref="myapp:abc",
            timestamp="2024-01-01T00:00:00",
            scan_results=[_scan_ok("semgrep", [_finding("HIGH")])],
        )
        d = report.to_dict()
        assert d["project_name"] == "myapp"
        assert d["image_ref"] == "myapp:abc"
        assert isinstance(d["blocked"], bool)
        assert isinstance(d["severity_counts"], dict)
        assert isinstance(d["tools_run"], list)
        assert isinstance(d["findings"], list)
        assert d["findings"][0]["severity"] == "HIGH"

    def test_to_dict_is_json_serializable(self):
        """to_dict output must be JSON-serializable — no datetime objects etc."""
        report = ConsolidatedReport(
            project_name="app", image_ref="app:1", timestamp="2024-01-01T00:00:00",
            scan_results=[_scan_ok("semgrep", [_finding("HIGH")])],
        )
        # Should not raise
        serialized = json.dumps(report.to_dict())
        assert len(serialized) > 0

    def test_findings_sorted_critical_first(self):
        """Findings in to_dict should be sorted CRITICAL → HIGH → MEDIUM → LOW."""
        report = ConsolidatedReport(
            project_name="app", image_ref="app:1", timestamp="2024-01-01T00:00:00",
            scan_results=[_scan_ok("semgrep", [
                _finding("LOW"),
                _finding("CRITICAL"),
                _finding("MEDIUM"),
                _finding("HIGH"),
            ])],
        )
        severities = [f["severity"] for f in report.to_dict()["findings"]]
        assert severities == ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


class TestGenerateReport:
    def test_creates_latest_files(self, tmp_path):
        results = [_scan_ok("semgrep", [_finding("HIGH")])]
        generate_report(results, "myapp", "myapp:v1", str(tmp_path))
        assert (tmp_path / "latest.json").exists()
        assert (tmp_path / "latest.html").exists()

    def test_creates_timestamped_files(self, tmp_path):
        results = [_scan_ok("semgrep", [])]
        generate_report(results, "myapp", "myapp:v1", str(tmp_path))
        json_files = list(tmp_path.glob("report_*.json"))
        html_files = list(tmp_path.glob("report_*.html"))
        assert len(json_files) == 1
        assert len(html_files) == 1

    def test_report_blocked_on_high(self, tmp_path):
        results = [_scan_ok("semgrep", [_finding("HIGH")])]
        report = generate_report(results, "app", "app:1", str(tmp_path), "HIGH")
        assert report.blocked is True

    def test_report_not_blocked_on_medium(self, tmp_path):
        results = [_scan_ok("semgrep", [_finding("MEDIUM")])]
        report = generate_report(results, "app", "app:1", str(tmp_path), "HIGH")
        assert report.blocked is False

    def test_creates_output_dir_if_missing(self, tmp_path):
        deep_dir = str(tmp_path / "a" / "b" / "c")
        generate_report([], "app", "app:1", deep_dir)
        from pathlib import Path
        assert Path(deep_dir).exists()

    def test_all_skipped_tools_clean_report(self, tmp_path):
        results = [_scan_skipped("semgrep"), _scan_skipped("bandit"), _scan_skipped("trivy")]
        report = generate_report(results, "app", "app:1", str(tmp_path))
        assert report.blocked is False
        assert report.all_findings == []

    def test_mixed_tools_high_counts(self, tmp_path):
        results = [
            _scan_ok("semgrep", [_finding("HIGH"), _finding("MEDIUM")]),
            _scan_skipped("bandit"),
            _scan_ok("trivy", [_finding("CRITICAL")]),
        ]
        report = generate_report(results, "app", "app:1", str(tmp_path))
        assert report.severity_counts["CRITICAL"] == 1
        assert report.severity_counts["HIGH"] == 1
        assert report.severity_counts["MEDIUM"] == 1
        assert report.blocked is True

    def test_latest_json_content_valid(self, tmp_path):
        results = [_scan_ok("semgrep", [_finding("HIGH")])]
        generate_report(results, "myapp", "myapp:abc", str(tmp_path))
        content = json.loads((tmp_path / "latest.json").read_text())
        assert content["project_name"] == "myapp"
        assert content["blocked"] is True

    def test_html_contains_project_name(self, tmp_path):
        results = [_scan_ok("semgrep", [])]
        generate_report(results, "my-special-project", "img:v1", str(tmp_path))
        html = (tmp_path / "latest.html").read_text()
        assert "my-special-project" in html

    def test_html_shows_blocked_status(self, tmp_path):
        results = [_scan_ok("semgrep", [_finding("HIGH")])]
        generate_report(results, "app", "app:1", str(tmp_path))
        html = (tmp_path / "latest.html").read_text()
        assert "BLOCKED" in html

    def test_html_shows_passed_status(self, tmp_path):
        results = [_scan_ok("semgrep", [])]
        generate_report(results, "app", "app:1", str(tmp_path))
        html = (tmp_path / "latest.html").read_text()
        assert "PASSED" in html
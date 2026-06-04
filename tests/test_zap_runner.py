"""
tests/test_zap_runner.py — backend/security/zap_runner (OWASP ZAP DAST).

Covers skip paths, the ZAP/Docker failure branches, JSON parsing, finding
normalisation + blocked logic, and the severity/HTML helpers. Docker is mocked;
the ZAP report is faked on disk.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

from backend.security.zap_runner import run_zap_baseline, _severity_gte, _strip_html

_RUN = "backend.security.zap_runner.subprocess.run"


def _cfg(zap=True, fail_on="CRITICAL"):
    return {"security": {"tools": {"owasp_zap": zap}, "zap_fail_on": fail_on}}


def test_skip_when_disabled():
    r = run_zap_baseline("http://x", {"security": {"tools": {}}})
    assert r.skipped and "owasp_zap disabled" in r.skip_reason


def test_skip_when_no_target():
    r = run_zap_baseline("", _cfg())
    assert r.skipped and "No target URL" in r.skip_reason


def test_internal_error_exit_2(tmp_path):
    with patch(_RUN, return_value=MagicMock(returncode=2, stderr="zap broke")):
        r = run_zap_baseline("http://x", _cfg(), output_dir=str(tmp_path))
    assert not r.success and "internal error" in r.error_message


def test_timeout(tmp_path):
    with patch(_RUN, side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=1)):
        r = run_zap_baseline("http://x", _cfg(), output_dir=str(tmp_path))
    assert not r.success and "timed out" in r.error_message


def test_docker_missing(tmp_path):
    with patch(_RUN, side_effect=FileNotFoundError):
        r = run_zap_baseline("http://x", _cfg(), output_dir=str(tmp_path))
    assert not r.success and "Docker not found" in r.error_message


def test_report_missing(tmp_path):
    with patch(_RUN, return_value=MagicMock(returncode=0, stderr="")):
        r = run_zap_baseline("http://x", _cfg(), output_dir=str(tmp_path))
    assert not r.success and "report not found" in r.error_message


def test_report_unparseable(tmp_path):
    (tmp_path / "zap-report.json").write_text("not json", encoding="utf-8")
    with patch(_RUN, return_value=MagicMock(returncode=0, stderr="")):
        r = run_zap_baseline("http://x", _cfg(), output_dir=str(tmp_path))
    assert not r.success and "Failed to parse" in r.error_message


def test_success_normalises_and_blocks(tmp_path):
    report = {"site": [{"@name": "http://x", "alerts": [
        {"riskcode": "3", "alert": "XSS", "desc": "<p>bad</p>", "solution": "<p>fix it</p>",
         "confidence": "High", "instances": [{"uri": "http://x/a", "evidence": "e"}]},
        {"riskcode": "1", "alert": "Info leak", "desc": "d", "solution": "s",
         "confidence": "Low", "instances": []},
    ]}]}
    (tmp_path / "zap-report.json").write_text(json.dumps(report), encoding="utf-8")
    with patch(_RUN, return_value=MagicMock(returncode=0, stderr="")):
        r = run_zap_baseline("http://x", _cfg(), output_dir=str(tmp_path))
    assert r.success
    assert r.severity_counts["CRITICAL"] == 1 and r.severity_counts["MEDIUM"] == 1
    assert r.blocked is True
    assert r.findings[0].description == "bad"          # HTML stripped


def test_success_not_blocked_below_threshold(tmp_path):
    report = {"site": [{"@name": "http://x", "alerts": [
        {"riskcode": "1", "alert": "Low thing", "desc": "d", "solution": "s",
         "confidence": "Low", "instances": [{"uri": "http://x"}]},
    ]}]}
    (tmp_path / "zap-report.json").write_text(json.dumps(report), encoding="utf-8")
    with patch(_RUN, return_value=MagicMock(returncode=0, stderr="")):
        r = run_zap_baseline("http://x", _cfg(fail_on="CRITICAL"), output_dir=str(tmp_path))
    assert r.success and r.blocked is False


def test_helpers():
    assert _severity_gte("CRITICAL", "HIGH") is True
    assert _severity_gte("LOW", "HIGH") is False
    assert _strip_html("<p>hello <b>world</b></p>") == "hello world"

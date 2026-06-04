"""
tests/test_sonarqube_extra.py — backend/security/sonarqube_runner (uncovered branches).

The configured run path (scanner/mvn/wait/issues), the quality gate, and config
resolution. subprocess + requests + shutil are mocked; the analysis-wait returns on
its first poll so there's no sleeping.
"""

from unittest.mock import MagicMock

from backend.security import sonarqube_runner as sq
from backend.security.sonarqube_runner import run_sonarqube, check_quality_gate, _build_sonar_config


def _env(monkeypatch, token="t", host="http://sonar"):
    for name, val in (("SONAR_TOKEN", token), ("SONAR_HOST_URL", host)):
        if val:
            monkeypatch.setenv(name, val)
        else:
            monkeypatch.delenv(name, raising=False)


def _cfg():
    return sq.SonarQubeConfig(host_url="http://s", token="t", project_key="k", project_name="n")


# ── config resolution ─────────────────────────────────────────────────────────

def test_build_config_none_without_env(monkeypatch):
    _env(monkeypatch, token="", host="")
    assert _build_sonar_config(".", {}) is None


def test_build_config_with_env(monkeypatch):
    _env(monkeypatch)
    cfg = _build_sonar_config(".", {"project": {"name": "My-App"}})
    assert cfg.project_key == "my_app" and cfg.host_url == "http://sonar"


# ── run_sonarqube flow ────────────────────────────────────────────────────────

def test_run_skipped_without_env(monkeypatch):
    _env(monkeypatch, token="", host="")
    assert run_sonarqube(".", {}).skipped


def test_run_scanner_failure(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(sq, "_run_sonar_scanner", lambda cfg: False)
    r = run_sonarqube(".", {})
    assert not r.success and "sonar-scanner failed" in r.error_message


def test_run_analysis_timeout(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(sq, "_run_sonar_scanner", lambda cfg: True)
    monkeypatch.setattr(sq, "_wait_for_analysis", lambda cfg: False)
    r = run_sonarqube(".", {})
    assert not r.success and "did not complete" in r.error_message


def test_run_success(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(sq, "_run_sonar_scanner", lambda cfg: True)
    monkeypatch.setattr(sq, "_wait_for_analysis", lambda cfg: True)
    monkeypatch.setattr(sq, "_fetch_issues", lambda cfg: [])
    assert run_sonarqube(".", {}).success


# ── scanner / mvn ─────────────────────────────────────────────────────────────

def test_scanner_used_when_present(monkeypatch):
    monkeypatch.setattr(sq.shutil, "which", lambda c: "/usr/bin/sonar-scanner" if c == "sonar-scanner" else None)
    monkeypatch.setattr(sq.subprocess, "run", lambda *a, **k: MagicMock(returncode=0))
    assert sq._run_sonar_scanner(_cfg()) is True


def test_scanner_falls_back_to_mvn(monkeypatch):
    monkeypatch.setattr(sq.shutil, "which", lambda c: "/usr/bin/mvn" if c == "mvn" else None)
    monkeypatch.setattr(sq.subprocess, "run", lambda *a, **k: MagicMock(returncode=0))
    assert sq._run_sonar_scanner(_cfg()) is True


def test_mvn_missing_returns_false(monkeypatch):
    monkeypatch.setattr(sq.shutil, "which", lambda c: None)
    assert sq._try_mvn_sonar(_cfg()) is False


# ── wait + fetch ──────────────────────────────────────────────────────────────

def test_wait_success(monkeypatch):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"current": {"status": "SUCCESS"}}
    monkeypatch.setattr(sq.requests, "get", lambda *a, **k: resp)
    assert sq._wait_for_analysis(_cfg()) is True


def test_wait_failed(monkeypatch):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"current": {"status": "FAILED"}}
    monkeypatch.setattr(sq.requests, "get", lambda *a, **k: resp)
    assert sq._wait_for_analysis(_cfg()) is False


def test_fetch_issues_maps_severity(monkeypatch):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"issues": [
        {"severity": "CRITICAL", "rule": "r", "message": "m", "component": "proj:app.py",
         "textRange": {"startLine": 5, "endLine": 6}, "type": "BUG", "effort": "5min"}]}
    monkeypatch.setattr(sq.requests, "get", lambda *a, **k: resp)
    issues = sq._fetch_issues(_cfg())
    assert len(issues) == 1 and issues[0].severity == "HIGH" and issues[0].file_path == "app.py"


# ── quality gate ──────────────────────────────────────────────────────────────

def test_gate_skipped_when_unconfigured(monkeypatch):
    _env(monkeypatch, token="", host="")
    ok, msg = check_quality_gate({})
    assert ok and "skipped" in msg


def test_gate_http_error(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(sq.requests, "get", lambda *a, **k: MagicMock(status_code=500))
    ok, msg = check_quality_gate({"project": {"name": "app"}})
    assert ok and "Could not fetch" in msg


def test_gate_passed(monkeypatch):
    _env(monkeypatch)
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"projectStatus": {"status": "OK"}}
    monkeypatch.setattr(sq.requests, "get", lambda *a, **k: resp)
    ok, msg = check_quality_gate({"project": {"name": "app"}})
    assert ok and "passed" in msg


def test_gate_failed(monkeypatch):
    _env(monkeypatch)
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"projectStatus": {"status": "ERROR",
                                                "conditions": [{"status": "ERROR", "metricKey": "coverage"}]}}
    monkeypatch.setattr(sq.requests, "get", lambda *a, **k: resp)
    ok, msg = check_quality_gate({"project": {"name": "app"}})
    assert not ok and "coverage" in msg

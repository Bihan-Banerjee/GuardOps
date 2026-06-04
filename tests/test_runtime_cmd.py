"""
tests/test_runtime_cmd.py — `guardops runtime-status`.

Covers the skip/error/clean/alerts paths and the --fail-on CI gate. The Loki query
(query_falco_alerts) is mocked.
"""

from types import SimpleNamespace
from unittest.mock import patch

from cli.commands.runtime_cmd import runtime_status_command


def _cfg():
    return {"monitoring": {"loki_url": "http://localhost:3100"}}


def _alert(sev="CRITICAL"):
    return SimpleNamespace(severity=sev, rule="Shell Spawned", pod_name="p1",
                           namespace="default", timestamp="2026-06-04T10:00:00Z", output="bad thing")


def _result(*, skipped=False, skip_reason="", success=True, error_message="",
            counts=None, alerts=None, above=False):
    return SimpleNamespace(
        skipped=skipped, skip_reason=skip_reason, success=success, error_message=error_message,
        severity_counts=counts or {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
        alerts=alerts or [], query_duration_seconds=0.4,
        has_alerts_above=lambda fail_on: above,
    )


def _run(result, args=None):
    with patch("cli.commands.runtime_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.runtime_cmd.query_falco_alerts", return_value=result):
        from click.testing import CliRunner
        return CliRunner().invoke(runtime_status_command, args or [])


def test_skipped_shows_setup_hint():
    r = _run(_result(skipped=True, skip_reason="runtime security disabled"))
    assert r.exit_code == 0
    assert "runtime security disabled" in r.output
    assert "enable Phase 7" in r.output


def test_query_failure_exits_one():
    r = _run(_result(success=False, error_message="loki unreachable"))
    assert r.exit_code == 1
    assert "Failed to query Loki" in r.output


def test_no_alerts_is_clean():
    r = _run(_result(alerts=[]))
    assert r.exit_code == 0
    assert "looks clean" in r.output


def test_no_alerts_gate_passes():
    r = _run(_result(alerts=[]), ["--fail-on", "CRITICAL"])
    assert r.exit_code == 0
    assert "gate passed" in r.output.lower()


def test_alerts_render_table():
    r = _run(_result(counts={"CRITICAL": 1, "HIGH": 0, "MEDIUM": 0, "LOW": 0}, alerts=[_alert()]))
    assert r.exit_code == 0
    assert "Shell Spawned" in r.output


def test_gate_fails_on_critical():
    res = _result(counts={"CRITICAL": 1, "HIGH": 0, "MEDIUM": 0, "LOW": 0}, alerts=[_alert()], above=True)
    r = _run(res, ["--fail-on", "CRITICAL"])
    assert r.exit_code == 1
    assert "gate FAILED" in r.output


def test_gate_passes_with_low_alerts():
    res = _result(counts={"CRITICAL": 0, "HIGH": 0, "MEDIUM": 1, "LOW": 0},
                  alerts=[_alert(sev="MEDIUM")], above=False)
    r = _run(res, ["--fail-on", "CRITICAL"])
    assert r.exit_code == 0
    assert "gate passed" in r.output.lower()

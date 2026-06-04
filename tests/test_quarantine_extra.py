"""
tests/test_quarantine_extra.py — quarantine_cmd uncovered branches.

kubectl-error / bad-JSON guards in the fetchers, the policies table, the env-display
line, the _release_pod variants, and the age formatter's other units.
"""

import subprocess
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cli.commands.quarantine_cmd import (
    quarantine_status_cmd, _get_quarantined_pods, _get_quarantine_policies,
    _release_pod, _age_str, _run_kubectl,
)

_K = "cli.commands.quarantine_cmd._run_kubectl"


def test_get_pods_kubectl_error():
    with patch(_K, return_value=(False, "", "a real error")):
        assert _get_quarantined_pods(["-n", "default"]) == []


def test_get_pods_bad_json():
    with patch(_K, return_value=(True, "not json", "")):
        assert _get_quarantined_pods(["-n", "default"]) == []


def test_get_policies_kubectl_error():
    with patch(_K, return_value=(False, "", "a real error")):
        assert _get_quarantine_policies(["-n", "default"]) == []


def test_get_policies_bad_json():
    with patch(_K, return_value=(True, "not json", "")):
        assert _get_quarantine_policies(["-n", "default"]) == []


def _base(monkeypatch):
    monkeypatch.setattr("cli.commands.quarantine_cmd.load_config",
                        lambda: {"kubernetes": {"namespace": "default"}})
    monkeypatch.setattr("cli.commands.quarantine_cmd.merge_with_defaults", lambda c: c)


def test_policies_table_renders(runner, monkeypatch):
    _base(monkeypatch)
    pols = [{"name": "np1", "namespace": "default", "fingerprint": "abc123def456ghi",
             "falco_rule": "Shell", "reason": "r", "created": ""}]
    monkeypatch.setattr("cli.commands.quarantine_cmd._get_quarantined_pods", lambda f: [])
    monkeypatch.setattr("cli.commands.quarantine_cmd._get_quarantine_policies", lambda f: pols)
    r = runner.invoke(quarantine_status_cmd, [])
    assert r.exit_code == 0 and "np1" in r.output


def test_env_display(runner, monkeypatch):
    _base(monkeypatch)
    monkeypatch.setattr("cli.commands.quarantine_cmd.resolve_namespace", lambda c, e: "staging")
    monkeypatch.setattr("cli.commands.quarantine_cmd._get_quarantined_pods", lambda f: [])
    monkeypatch.setattr("cli.commands.quarantine_cmd._get_quarantine_policies", lambda f: [])
    r = runner.invoke(quarantine_status_cmd, ["--env", "staging"])
    assert r.exit_code == 0 and "env=" in r.output


def test_release_pod_no_policies():
    def fake(args):
        return (True, "", "")    # get returns no names; label succeeds
    with patch(_K, side_effect=fake):
        _release_pod(pod_name="p1", namespace="default")   # no raise


def test_release_pod_delete_failure_warns():
    def fake(args):
        if args[0] == "get":
            return (True, "np1", "")
        if args[0] == "delete":
            return (False, "", "cannot delete")
        return (True, "", "")
    with patch(_K, side_effect=fake):
        _release_pod(pod_name="p1", namespace="default")


def test_release_pod_label_not_found_warns():
    def fake(args):
        if args[0] == "label":
            return (False, "", "pods 'p1' not found")
        return (True, "", "")
    with patch(_K, side_effect=fake):
        _release_pod(pod_name="p1", namespace="default")


def test_release_pod_label_error_exits():
    def fake(args):
        if args[0] == "label":
            return (False, "", "permission denied")
        return (True, "", "")
    with patch(_K, side_effect=fake):
        with pytest.raises(SystemExit):
            _release_pod(pod_name="p1", namespace="default")


def test_age_str_seconds_and_days():
    now = datetime.now(timezone.utc)
    assert _age_str((now - timedelta(seconds=10)).isoformat()).endswith("s")
    assert _age_str((now - timedelta(minutes=5)).isoformat()).endswith("m")
    assert _age_str((now - timedelta(days=3)).isoformat()).endswith("d")


# ── kubectl-failure WITH output (warn-then-empty, not the "no resources" path) ──

def test_get_pods_real_error_with_stdout_warns():
    # not ok, stderr is a genuine error, and stdout is non-empty → warn + [].
    with patch(_K, return_value=(False, "partial", "connection refused")):
        assert _get_quarantined_pods(["-n", "default"]) == []


def test_get_policies_real_error_with_stdout_warns():
    with patch(_K, return_value=(False, "partial", "connection refused")):
        assert _get_quarantine_policies(["-n", "default"]) == []


# ── _run_kubectl subprocess wrapper (direct) ────────────────────────────────────

def test_run_kubectl_success():
    fake = SimpleNamespace(returncode=0, stdout="out\n", stderr="err\n")
    with patch("cli.commands.quarantine_cmd.subprocess.run", return_value=fake):
        ok, out, err = _run_kubectl(["get", "pods"])
    assert ok is True and out == "out" and err == "err"


def test_run_kubectl_timeout():
    with patch("cli.commands.quarantine_cmd.subprocess.run",
               side_effect=subprocess.TimeoutExpired("kubectl", 1)):
        ok, out, err = _run_kubectl(["get", "pods"])
    assert ok is False and "timed out" in err


def test_run_kubectl_not_found():
    with patch("cli.commands.quarantine_cmd.subprocess.run", side_effect=FileNotFoundError()):
        ok, out, err = _run_kubectl(["get", "pods"])
    assert ok is False and "not found" in err


def test_run_kubectl_unexpected_error():
    with patch("cli.commands.quarantine_cmd.subprocess.run", side_effect=RuntimeError("boom")):
        ok, out, err = _run_kubectl(["get", "pods"])
    assert ok is False and "Unexpected error" in err

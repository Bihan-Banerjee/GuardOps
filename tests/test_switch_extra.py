"""
tests/test_switch_extra.py — switch_cmd uncovered branches.

The real kubectl wrappers, _apply_traffic_service, the JSON-decode guards, and the
not-ready / create command branches.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from cli.commands import switch_cmd as sc
from cli.commands.switch_cmd import switch_command

_RUN = "cli.commands.switch_cmd.subprocess.run"
READY = [{"name": "p", "ready": True, "phase": "Running"}]
NOTREADY = [{"name": "p", "ready": False, "phase": "Pending"}]


def test_run_kubectl_success():
    with patch(_RUN, return_value=MagicMock(returncode=0, stdout="out ", stderr="")):
        ok, out, _ = sc._run_kubectl(["get", "pods"])
    assert ok and out == "out"


def test_run_kubectl_timeout():
    with patch(_RUN, side_effect=subprocess.TimeoutExpired(cmd="kubectl", timeout=1)):
        ok, _, err = sc._run_kubectl(["get", "pods"])
    assert not ok and "timed out" in err


def test_run_kubectl_not_found():
    with patch(_RUN, side_effect=FileNotFoundError):
        ok, _, err = sc._run_kubectl(["get", "pods"])
    assert not ok and "not found" in err


def test_run_kubectl_unexpected():
    with patch(_RUN, side_effect=RuntimeError("x")):
        ok, _, err = sc._run_kubectl(["get", "pods"])
    assert not ok and "Unexpected error" in err


def test_apply_stdin_success_and_failure():
    with patch(_RUN, return_value=MagicMock(returncode=0, stdout="", stderr="")):
        assert sc._kubectl_apply_stdin("yaml")[0] is True
    with patch(_RUN, return_value=MagicMock(returncode=1, stdout="", stderr="bad")):
        ok, _, err = sc._kubectl_apply_stdin("yaml")
        assert not ok and err == "bad"
    with patch(_RUN, side_effect=FileNotFoundError):
        assert sc._kubectl_apply_stdin("yaml")[0] is False


def test_apply_traffic_service_exits_on_failure():
    with patch("cli.commands.switch_cmd._kubectl_apply_stdin", return_value=(False, "", "apply err")):
        with pytest.raises(SystemExit):
            sc._apply_traffic_service(svc_name="svc", namespace="ns", project_name="app",
                                      slot="green", exists=True)


def test_get_slot_pods_bad_json():
    with patch("cli.commands.switch_cmd._run_kubectl", return_value=(True, "not json", "")):
        assert sc._get_slot_pods("ns", "blue", "app") == []


def test_get_current_active_slot_bad_json():
    with patch("cli.commands.switch_cmd._run_kubectl", return_value=(True, "not json", "")):
        assert sc._get_current_active_slot("svc", "ns") is None


def _cmd_patches(slot_pods, active):
    return (
        patch("cli.commands.switch_cmd.load_config", return_value={"project": {"name": "app"}}),
        patch("cli.commands.switch_cmd.resolve_namespace", return_value="staging"),
        patch("cli.commands.switch_cmd._get_slot_pods", side_effect=lambda ns, s, p: slot_pods),
        patch("cli.commands.switch_cmd._apply_traffic_service"),
        patch("cli.commands.switch_cmd._get_current_active_slot", **active),
    )


def test_switch_target_not_ready_warns(runner):
    ps = _cmd_patches(NOTREADY, {"side_effect": ["blue", "green"]})
    with ps[0], ps[1], ps[2], ps[3], ps[4]:
        r = runner.invoke(switch_command, ["--slot", "green"])
    assert r.exit_code == 0 and "none are Ready" in r.output


def test_switch_dry_run_create(runner):
    ps = _cmd_patches(READY, {"return_value": None})   # service does not exist yet
    with ps[0], ps[1], ps[2], ps[3], ps[4]:
        r = runner.invoke(switch_command, ["--slot", "green", "--dry-run"])
    assert r.exit_code == 0 and "CREATE" in r.output

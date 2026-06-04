"""
tests/test_switch_cmd.py — `guardops switch` (blue-green traffic switch).

Two layers: the command flow (mocking the slot/service helpers) and the helpers
themselves (mocking _run_kubectl). kubectl is never really invoked.
"""

import json
from unittest.mock import patch

from cli.commands.switch_cmd import (
    switch_command,
    _build_service_manifest,
    _get_slot_pods,
    _get_current_active_slot,
)

READY = [{"name": "p1", "ready": True, "phase": "Running"}]


def _cfg():
    return {"project": {"name": "guardops-app"}}


def _slot_pods(blue, green):
    def fn(_ns, slot, _proj):
        return blue if slot == "blue" else green
    return fn


# ── command flow ──────────────────────────────────────────────────────────────

def test_no_target_pods_exits_one(runner):
    with patch("cli.commands.switch_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.switch_cmd.resolve_namespace", return_value="staging"), \
         patch("cli.commands.switch_cmd._get_slot_pods", side_effect=_slot_pods(READY, [])), \
         patch("cli.commands.switch_cmd._get_current_active_slot", return_value="blue"):
        result = runner.invoke(switch_command, ["--slot", "green"])
    assert result.exit_code == 1
    assert "No pods found" in result.output


def test_already_routed_no_change(runner):
    with patch("cli.commands.switch_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.switch_cmd.resolve_namespace", return_value="staging"), \
         patch("cli.commands.switch_cmd._get_slot_pods", side_effect=_slot_pods(READY, READY)), \
         patch("cli.commands.switch_cmd._get_current_active_slot", return_value="green"):
        result = runner.invoke(switch_command, ["--slot", "green"])
    assert result.exit_code == 0
    assert "already routed" in result.output.lower()


def test_dry_run_previews_only(runner):
    with patch("cli.commands.switch_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.switch_cmd.resolve_namespace", return_value="staging"), \
         patch("cli.commands.switch_cmd._get_slot_pods", side_effect=_slot_pods(READY, READY)), \
         patch("cli.commands.switch_cmd._apply_traffic_service") as mock_apply, \
         patch("cli.commands.switch_cmd._get_current_active_slot", return_value="blue"):
        result = runner.invoke(switch_command, ["--slot", "green", "--dry-run"])
    assert result.exit_code == 0
    assert "Dry run" in result.output
    mock_apply.assert_not_called()


def test_successful_switch(runner):
    with patch("cli.commands.switch_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.switch_cmd.resolve_namespace", return_value="staging"), \
         patch("cli.commands.switch_cmd._get_slot_pods", side_effect=_slot_pods(READY, READY)), \
         patch("cli.commands.switch_cmd._apply_traffic_service") as mock_apply, \
         patch("cli.commands.switch_cmd._get_current_active_slot", side_effect=["blue", "green"]):
        result = runner.invoke(switch_command, ["--slot", "green"])
    assert result.exit_code == 0, result.output
    assert "Traffic switched to" in result.output
    mock_apply.assert_called_once()


def test_switch_verify_mismatch_exits_one(runner):
    with patch("cli.commands.switch_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.switch_cmd.resolve_namespace", return_value="staging"), \
         patch("cli.commands.switch_cmd._get_slot_pods", side_effect=_slot_pods(READY, READY)), \
         patch("cli.commands.switch_cmd._apply_traffic_service"), \
         patch("cli.commands.switch_cmd._get_current_active_slot", side_effect=["blue", "blue"]):
        result = runner.invoke(switch_command, ["--slot", "green"])
    assert result.exit_code == 1
    assert "expected" in result.output.lower()


# ── helpers ───────────────────────────────────────────────────────────────────

def test_build_service_manifest_targets_slot():
    m = _build_service_manifest("guardops-app", "staging", "guardops-app", "green")
    assert "name: guardops-app" in m
    assert "guardops.io/slot: green" in m
    assert "guardops.io/active-slot: green" in m


def test_get_slot_pods_parses_ready():
    pod_json = json.dumps({"items": [
        {"metadata": {"name": "blue-1"},
         "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]}},
    ]})
    with patch("cli.commands.switch_cmd._run_kubectl", return_value=(True, pod_json, "")):
        pods = _get_slot_pods("staging", "blue", "guardops-app")
    assert len(pods) == 1 and pods[0]["ready"] is True and pods[0]["slot"] == "blue"


def test_get_slot_pods_empty_on_kubectl_failure():
    with patch("cli.commands.switch_cmd._run_kubectl", return_value=(False, "", "boom")):
        assert _get_slot_pods("staging", "blue", "guardops-app") == []


def test_get_current_active_slot_reads_annotation():
    svc_json = json.dumps({"metadata": {"annotations": {"guardops.io/active-slot": "green"}}})
    with patch("cli.commands.switch_cmd._run_kubectl", return_value=(True, svc_json, "")):
        assert _get_current_active_slot("guardops-app", "staging") == "green"


def test_get_current_active_slot_none_when_missing():
    with patch("cli.commands.switch_cmd._run_kubectl", return_value=(False, "", "NotFound")):
        assert _get_current_active_slot("guardops-app", "staging") is None

"""
tests/test_quarantine_cmd.py — `guardops quarantine-status`.

Command flow (idle / JSON / table / --release guards) plus the kubectl-backed
helpers and the age formatter. kubectl is mocked throughout.
"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from cli.commands.quarantine_cmd import (
    quarantine_status_cmd,
    _get_quarantined_pods,
    _get_quarantine_policies,
    _release_pod,
    _age_str,
)

_CFG = {"project": {"name": "demo"}, "kubernetes": {"namespace": "default"}}


def _base():
    return (
        patch("cli.commands.quarantine_cmd.load_config", return_value=_CFG),
        patch("cli.commands.quarantine_cmd.merge_with_defaults", side_effect=lambda c: c),
    )


# ── command flow ──────────────────────────────────────────────────────────────

def test_nothing_quarantined(runner):
    lc, md = _base()
    with lc, md, \
         patch("cli.commands.quarantine_cmd._get_quarantined_pods", return_value=[]), \
         patch("cli.commands.quarantine_cmd._get_quarantine_policies", return_value=[]):
        result = runner.invoke(quarantine_status_cmd, [])
    assert result.exit_code == 0
    assert "No quarantined pods" in result.output


def test_json_output(runner):
    lc, md = _base()
    with lc, md, \
         patch("cli.commands.quarantine_cmd._get_quarantined_pods", return_value=[{"name": "p1"}]), \
         patch("cli.commands.quarantine_cmd._get_quarantine_policies", return_value=[{"name": "np1"}]):
        result = runner.invoke(quarantine_status_cmd, ["--json-output"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["total_quarantined_pods"] == 1
    assert data["total_active_policies"] == 1


def test_pods_table_shows_release_hint(runner):
    pods = [{"name": "app-xyz", "namespace": "default", "node": "n1",
             "phase": "Running", "created": "", "quarantine_since": "", "annotations": {}}]
    lc, md = _base()
    with lc, md, \
         patch("cli.commands.quarantine_cmd._get_quarantined_pods", return_value=pods), \
         patch("cli.commands.quarantine_cmd._get_quarantine_policies", return_value=[]):
        result = runner.invoke(quarantine_status_cmd, [])
    assert result.exit_code == 0
    assert "app-xyz" in result.output
    assert "--release" in result.output


def test_release_requires_specific_namespace(runner):
    lc, md = _base()
    with lc, md:
        result = runner.invoke(quarantine_status_cmd, ["--release", "p1", "-A"])
    assert result.exit_code == 1
    assert "requires a specific namespace" in result.output


def test_release_invokes_release_pod(runner):
    lc, md = _base()
    with lc, md, patch("cli.commands.quarantine_cmd._release_pod") as mock_rel:
        result = runner.invoke(quarantine_status_cmd, ["--release", "my-pod", "--namespace", "default"])
    assert result.exit_code == 0
    mock_rel.assert_called_once()
    assert mock_rel.call_args.kwargs["pod_name"] == "my-pod"


# ── helpers ───────────────────────────────────────────────────────────────────

def test_get_quarantined_pods_parses():
    pod_json = json.dumps({"items": [
        {"metadata": {"name": "p1", "namespace": "default", "labels": {}, "annotations": {}},
         "spec": {"nodeName": "n1"}, "status": {"phase": "Running"}},
    ]})
    with patch("cli.commands.quarantine_cmd._run_kubectl", return_value=(True, pod_json, "")):
        pods = _get_quarantined_pods(["-n", "default"])
    assert len(pods) == 1 and pods[0]["name"] == "p1" and pods[0]["node"] == "n1"


def test_get_quarantined_pods_empty_on_no_resources():
    with patch("cli.commands.quarantine_cmd._run_kubectl", return_value=(False, "", "No resources found")):
        assert _get_quarantined_pods(["-n", "default"]) == []


def test_get_quarantine_policies_parses_annotations():
    np_json = json.dumps({"items": [
        {"metadata": {"name": "np1", "namespace": "default",
                      "annotations": {"guardops.io/falco-rule": "Terminal shell in container"}}},
    ]})
    with patch("cli.commands.quarantine_cmd._run_kubectl", return_value=(True, np_json, "")):
        pols = _get_quarantine_policies(["-n", "default"])
    assert len(pols) == 1 and pols[0]["falco_rule"] == "Terminal shell in container"


def test_age_str_variants():
    assert _age_str("") == "-"
    assert _age_str("not-a-date") == "-"
    two_h_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert _age_str(two_h_ago).endswith("h")


def test_release_pod_deletes_policies_and_unlabels():
    seen = []

    def fake(args):
        seen.append(args[0])
        if args[0] == "get":
            return (True, "np1 np2", "")
        return (True, "", "")     # delete + label both succeed

    with patch("cli.commands.quarantine_cmd._run_kubectl", side_effect=fake):
        _release_pod(pod_name="p1", namespace="default")
    assert "delete" in seen        # deleted the NetworkPolicies
    assert "label" in seen         # removed the quarantine label

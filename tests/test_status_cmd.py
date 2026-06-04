"""
tests/test_status_cmd.py — `guardops status`.

Renders deployment + pod health; a missing deployment guides the user to deploy.
kubectl-backed helpers are mocked.
"""

from unittest.mock import patch

from cli.commands.status_cmd import status_command


def _cfg():
    return {"project": {"name": "demo"}, "kubernetes": {"namespace": "default"}}


def test_deployment_not_found(runner):
    with patch("cli.commands.status_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.status_cmd.get_deployment_status", return_value=None):
        result = runner.invoke(status_command, [])
    assert result.exit_code == 0
    assert "not found" in result.output
    assert "guardops deploy" in result.output


def test_status_with_pods(runner):
    status = {"name": "demo", "namespace": "default",
              "desired_replicas": 2, "ready_replicas": 2, "available_replicas": 2}
    pods = [{"name": "demo-abc", "phase": "Running", "ready": True, "restarts": 0, "node": "n1"}]
    with patch("cli.commands.status_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.status_cmd.get_deployment_status", return_value=status), \
         patch("cli.commands.status_cmd.get_pods", return_value=pods):
        result = runner.invoke(status_command, [])
    assert result.exit_code == 0
    assert "demo-abc" in result.output


def test_status_no_pods(runner):
    status = {"name": "demo", "namespace": "default",
              "desired_replicas": 1, "ready_replicas": 0, "available_replicas": 0}
    with patch("cli.commands.status_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.status_cmd.get_deployment_status", return_value=status), \
         patch("cli.commands.status_cmd.get_pods", return_value=[]):
        result = runner.invoke(status_command, [])
    assert result.exit_code == 0
    assert "No pods found" in result.output

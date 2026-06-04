"""
tests/test_logs_cmd.py — `guardops logs`.

Selects the most recent running pod (or an explicit one) and streams via kubectl;
no pods exits 1. get_pods + subprocess are mocked (no real streaming).
"""

from unittest.mock import patch

from cli.commands.logs_cmd import logs_command


def _cfg():
    return {"project": {"name": "demo"}, "kubernetes": {"namespace": "default"}}


def test_no_pods_exits_one(runner):
    with patch("cli.commands.logs_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.logs_cmd.get_pods", return_value=[]):
        result = runner.invoke(logs_command, [])
    assert result.exit_code == 1
    assert "No pods found" in result.output


def test_streams_running_pod(runner):
    pods = [{"name": "demo-pending", "phase": "Pending"},
            {"name": "demo-xyz", "phase": "Running"}]
    with patch("cli.commands.logs_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.logs_cmd.get_pods", return_value=pods), \
         patch("cli.commands.logs_cmd.subprocess.run") as mock_run:
        result = runner.invoke(logs_command, [])
    assert result.exit_code == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[:2] == ["kubectl", "logs"]
    assert "demo-xyz" in cmd          # picked the Running pod, not the Pending one


def test_explicit_pod(runner):
    with patch("cli.commands.logs_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.logs_cmd.subprocess.run") as mock_run:
        result = runner.invoke(logs_command, ["--pod", "my-pod"])
    assert result.exit_code == 0
    assert "my-pod" in mock_run.call_args[0][0]

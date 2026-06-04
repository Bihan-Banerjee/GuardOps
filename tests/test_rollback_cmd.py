"""
tests/test_rollback_cmd.py — `guardops rollback`.

Helm history is shown first; rolling back needs >= 2 revisions; a failed rollback
exits non-zero. Helm is mocked.
"""

from types import SimpleNamespace
from unittest.mock import patch

from cli.commands.rollback_cmd import rollback_command


def _cfg():
    return {"project": {"name": "demo"}, "kubernetes": {"namespace": "default"}}


def test_no_release_exits_one(runner):
    with patch("cli.commands.rollback_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.rollback_cmd.get_helm_history", return_value=[]):
        result = runner.invoke(rollback_command, [])
    assert result.exit_code == 1
    assert "No Helm release" in result.output


def test_history_flag_shows_and_exits(runner):
    hist = [{"revision": 1, "status": "deployed", "updated": "now", "chart": "c", "description": "d"}]
    with patch("cli.commands.rollback_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.rollback_cmd.get_helm_history", return_value=hist):
        result = runner.invoke(rollback_command, ["--history"])
    assert result.exit_code == 0
    assert "Release History" in result.output


def test_single_revision_nothing_to_roll_back(runner):
    with patch("cli.commands.rollback_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.rollback_cmd.get_helm_history", return_value=[{"revision": 1}]):
        result = runner.invoke(rollback_command, [])
    assert result.exit_code == 0
    assert "nothing to roll back" in result.output.lower()


def test_rollback_success(runner):
    hist = [{"revision": 1}, {"revision": 2}]
    ok = SimpleNamespace(success=True, rolled_back_to=1, error_message="")
    with patch("cli.commands.rollback_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.rollback_cmd.get_helm_history", return_value=hist), \
         patch("cli.commands.rollback_cmd.rollback_helm", return_value=ok):
        result = runner.invoke(rollback_command, [])
    assert result.exit_code == 0
    assert "Rolled back" in result.output


def test_rollback_failure_exits_one(runner):
    hist = [{"revision": 1}, {"revision": 2}]
    bad = SimpleNamespace(success=False, rolled_back_to=None, error_message="boom")
    with patch("cli.commands.rollback_cmd.load_config", return_value=_cfg()), \
         patch("cli.commands.rollback_cmd.get_helm_history", return_value=hist), \
         patch("cli.commands.rollback_cmd.rollback_helm", return_value=bad):
        result = runner.invoke(rollback_command, [])
    assert result.exit_code == 1
    assert "Rollback failed" in result.output

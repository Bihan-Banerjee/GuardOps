"""
tests/test_system_utils.py — cli/utils/system.

The subprocess + dependency helpers every command relies on. subprocess and
shutil.which are mocked; the missing-binary / timeout paths exit via SystemExit.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from cli.utils import system


def test_run_command_success():
    with patch("cli.utils.system.subprocess.run", return_value=MagicMock(returncode=0)):
        assert system.run_command(["echo", "hi"]).returncode == 0


def test_run_command_missing_binary_exits():
    with patch("cli.utils.system.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(SystemExit):
            system.run_command(["nope"])


def test_run_command_timeout_exits():
    with patch("cli.utils.system.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
        with pytest.raises(SystemExit):
            system.run_command(["slow"])


def test_run_command_or_exit_ok():
    with patch("cli.utils.system.subprocess.run", return_value=MagicMock(returncode=0)):
        assert system.run_command_or_exit(["x"], "failed").returncode == 0


def test_run_command_or_exit_nonzero_exits():
    with patch("cli.utils.system.subprocess.run",
               return_value=MagicMock(returncode=2, stderr="boom")):
        with pytest.raises(SystemExit):
            system.run_command_or_exit(["x"], "it failed")


def test_check_dependency_present(monkeypatch):
    monkeypatch.setattr("cli.utils.system.shutil.which", lambda c: f"/usr/bin/{c}")
    assert system.check_dependency("docker") is True


def test_check_dependency_missing(monkeypatch):
    monkeypatch.setattr("cli.utils.system.shutil.which", lambda c: None)
    assert system.check_dependency("docker", "install it") is False


def test_check_all_dependencies(monkeypatch):
    monkeypatch.setattr("cli.utils.system.shutil.which",
                        lambda c: "/usr/bin/x" if c == "docker" else None)
    assert system.check_all_dependencies([("docker", ""), ("kubectl", "")]) is False

    monkeypatch.setattr("cli.utils.system.shutil.which", lambda c: f"/usr/bin/{c}")
    assert system.check_all_dependencies([("docker", ""), ("kubectl", "")]) is True


def test_get_command_output_strips(monkeypatch):
    with patch("cli.utils.system.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="  hello \n")):
        assert system.get_command_output(["x"]) == "hello"


def test_get_command_output_default_on_failure(monkeypatch):
    with patch("cli.utils.system.subprocess.run",
               return_value=MagicMock(returncode=1, stdout="")):
        assert system.get_command_output(["x"], default="fallback") == "fallback"

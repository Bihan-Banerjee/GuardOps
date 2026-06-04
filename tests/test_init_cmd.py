"""
tests/test_init_cmd.py — `guardops init`.

Scaffolds .guardops.yaml (+ sample Dockerfile / .env.example) in the cwd; validates
the project name; refuses to clobber an existing config without --force. Runs in an
isolated filesystem; dependency checks are mocked.
"""

from pathlib import Path
from unittest.mock import patch

from cli.commands.init_cmd import init_command


def test_init_creates_config(runner):
    with runner.isolated_filesystem():
        with patch("cli.commands.init_cmd.check_all_dependencies", return_value=True), \
             patch("cli.commands.init_cmd.get_command_output", return_value="k3d-guardops-local"):
            result = runner.invoke(init_command, ["--name", "demo", "--cloud", "local"])
        assert result.exit_code == 0, result.output
        assert Path(".guardops.yaml").exists()
        assert Path("Dockerfile").exists()       # sample scaffolded
        assert "Created" in result.output


def test_init_rejects_invalid_name(runner):
    with runner.isolated_filesystem():
        with patch("cli.commands.init_cmd.check_all_dependencies", return_value=True), \
             patch("cli.commands.init_cmd.get_command_output", return_value=""):
            result = runner.invoke(init_command, ["--name", "Bad_Name"])
        assert result.exit_code == 1
        assert "lowercase" in result.output


def test_init_missing_deps_exits_one(runner):
    with runner.isolated_filesystem():
        with patch("cli.commands.init_cmd.check_all_dependencies", return_value=False):
            result = runner.invoke(init_command, ["--name", "demo"])
        assert result.exit_code == 1
        assert "install the missing tools" in result.output.lower()


def test_init_keeps_existing_config_on_no(runner):
    with runner.isolated_filesystem():
        Path(".guardops.yaml").write_text("project:\n  name: old\n", encoding="utf-8")
        with patch("cli.commands.init_cmd.check_all_dependencies", return_value=True), \
             patch("cli.commands.init_cmd.get_command_output", return_value=""):
            result = runner.invoke(init_command, ["--name", "demo"], input="n\n")
        assert result.exit_code == 0
        assert "Keeping existing" in result.output
        # the original content is untouched
        assert "old" in Path(".guardops.yaml").read_text(encoding="utf-8")

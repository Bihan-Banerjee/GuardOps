"""
tests/test_main_cli.py — v1.0.0.

Smoke test for the root CLI group: --version works and every subcommand is wired
(a broken import in any command would fail --help here).
"""

from cli.main import cli


def test_version(runner):
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "guardops" in result.output.lower()


def test_help_lists_core_commands(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ("deploy", "scan", "rollback", "doctor", "admission", "dashboard",
                "runtime-status", "sync-status", "verify-image", "sbom"):
        assert cmd in result.output, f"{cmd} missing from --help"

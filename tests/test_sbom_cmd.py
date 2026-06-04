"""
tests/test_sbom_cmd.py — `guardops sbom`.

Exits 0 and lists package count + formats on success; syft missing or a failure
exits 1. syft is mocked.
"""

from types import SimpleNamespace
from unittest.mock import patch

from cli.commands.sbom_cmd import sbom_command


def _res(success=True, skipped=False, skip_reason="", error_message="",
         package_count=0, formats=None):
    return SimpleNamespace(success=success, skipped=skipped, skip_reason=skip_reason,
                           error_message=error_message, package_count=package_count,
                           formats=formats or {})


def test_syft_missing_exits_one(runner):
    with patch("cli.commands.sbom_cmd.run_syft",
               return_value=_res(success=False, skipped=True, skip_reason="syft not installed")):
        result = runner.invoke(sbom_command, ["guardops-app:latest"])
    assert result.exit_code == 1
    assert "syft not installed" in result.output


def test_generation_failure_exits_one(runner):
    with patch("cli.commands.sbom_cmd.run_syft",
               return_value=_res(success=False, error_message="boom")):
        result = runner.invoke(sbom_command, ["guardops-app:latest"])
    assert result.exit_code == 1
    assert "FAILED" in result.output


def test_sbom_success_lists_packages(runner):
    res = _res(success=True, package_count=42,
               formats={"cyclonedx": "security/reports/a.json", "spdx": "security/reports/b.json"})
    with patch("cli.commands.sbom_cmd.run_syft", return_value=res):
        result = runner.invoke(sbom_command, ["guardops-app:latest"])
    assert result.exit_code == 0
    assert "42 packages" in result.output
    assert "cyclonedx" in result.output

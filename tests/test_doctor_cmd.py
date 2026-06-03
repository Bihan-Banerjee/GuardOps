"""
tests/test_doctor_cmd.py — v1.0.0.

`guardops doctor` is the first-run preflight. It must exit non-zero only when a
REQUIRED core tool (docker/kubectl/helm) is missing, and surface install hints for
everything that's absent without ever raising.
"""

from unittest.mock import patch

from cli.commands.doctor_cmd import doctor_command

_ALL_TOOLS = {"docker", "kubectl", "helm", "semgrep", "bandit", "trivy",
              "git", "cosign", "syft", "aws", "terraform"}


def _which(present):
    """Return a fake shutil.which that resolves only the names in `present`."""
    return lambda name: f"/usr/bin/{name}" if name in present else None


def test_all_present_exits_zero(runner):
    with patch("cli.commands.doctor_cmd.shutil.which", side_effect=_which(_ALL_TOOLS)), \
         patch("cli.commands.doctor_cmd.config_exists", return_value=True):
        result = runner.invoke(doctor_command, [])
    assert result.exit_code == 0, result.output
    assert "All required tools present" in result.output


def test_missing_required_exits_one(runner):
    # docker missing → core requirement unmet → exit 1
    present = _ALL_TOOLS - {"docker"}
    with patch("cli.commands.doctor_cmd.shutil.which", side_effect=_which(present)), \
         patch("cli.commands.doctor_cmd.config_exists", return_value=True):
        result = runner.invoke(doctor_command, [])
    assert result.exit_code == 1, result.output
    assert "docker" in result.output
    assert "1 required tool missing" in result.output


def test_missing_optional_still_exits_zero(runner):
    # only scanners/cloud missing → recommended, not required → exit 0
    present = {"docker", "kubectl", "helm"}
    with patch("cli.commands.doctor_cmd.shutil.which", side_effect=_which(present)), \
         patch("cli.commands.doctor_cmd.config_exists", return_value=True):
        result = runner.invoke(doctor_command, [])
    assert result.exit_code == 0, result.output
    # missing optional tools still show an install hint
    assert "trivy" in result.output
    assert "pip install semgrep" in result.output


def test_missing_config_warns_not_fatal(runner):
    with patch("cli.commands.doctor_cmd.shutil.which", side_effect=_which(_ALL_TOOLS)), \
         patch("cli.commands.doctor_cmd.config_exists", return_value=False):
        result = runner.invoke(doctor_command, [])
    assert result.exit_code == 0, result.output
    assert "guardops init" in result.output

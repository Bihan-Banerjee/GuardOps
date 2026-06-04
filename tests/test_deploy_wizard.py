"""
tests/test_deploy_wizard.py — Phase 13.

Covers the interactive default-vs-custom chooser for `guardops deploy`:
  - should_offer_wizard gate (TTY / CI / --yes / -i / explicit-flag detection)
  - run_deploy_wizard default / custom / cancel paths (prompts monkeypatched)
  - to_cli_args / equivalent_command rendering (the "teach the flags" output)
  - deploy_command integration: non-TTY never prompts (CI-safe), -i forces it,
    cancel exits 0 without deploying.
"""

import sys
from unittest.mock import patch

from click.core import ParameterSource

import cli.commands._deploy_wizard as wiz
from cli.commands._deploy_wizard import DeployOptions
from cli.commands.deploy_cmd import deploy_command


# ── helpers ─────────────────────────────────────────────────────────────────--

class _FakeCtx:
    """Stand-in for a Click context: reports which params came from the CLI."""
    def __init__(self, commandline=()):
        self._cl = set(commandline)

    def get_parameter_source(self, name):
        return ParameterSource.COMMANDLINE if name in self._cl else ParameterSource.DEFAULT


class _FakeTTY:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


def _queue(values):
    """Return a callable that yields the next scripted answer on each call."""
    it = iter(values)
    return lambda *a, **k: next(it)


def _interactive_terminal(monkeypatch):
    """Make should_offer_wizard see an interactive, non-CI terminal."""
    monkeypatch.setattr(sys, "stdin", _FakeTTY(True))
    monkeypatch.setattr(sys, "stdout", _FakeTTY(True))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


# ── should_offer_wizard ────────────────────────────────────────────────────--

def test_offer_when_interactive_flag_even_in_ci(monkeypatch):
    monkeypatch.setenv("CI", "true")
    assert wiz.should_offer_wizard(_FakeCtx(), interactive=True, assume_yes=False) is True


def test_no_offer_when_assume_yes(monkeypatch):
    _interactive_terminal(monkeypatch)
    assert wiz.should_offer_wizard(_FakeCtx(), interactive=False, assume_yes=True) is False


def test_no_offer_in_ci(monkeypatch):
    _interactive_terminal(monkeypatch)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert wiz.should_offer_wizard(_FakeCtx(), interactive=False, assume_yes=False) is False


def test_no_offer_when_not_a_tty(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(sys, "stdin", _FakeTTY(False))
    monkeypatch.setattr(sys, "stdout", _FakeTTY(True))
    assert wiz.should_offer_wizard(_FakeCtx(), interactive=False, assume_yes=False) is False


def test_no_offer_when_a_flag_was_typed(monkeypatch):
    _interactive_terminal(monkeypatch)
    ctx = _FakeCtx(commandline=["env"])  # user typed --env
    assert wiz.should_offer_wizard(ctx, interactive=False, assume_yes=False) is False


def test_offer_on_bare_interactive_deploy(monkeypatch):
    _interactive_terminal(monkeypatch)
    assert wiz.should_offer_wizard(_FakeCtx(), interactive=False, assume_yes=False) is True


def test_no_offer_when_isatty_raises(monkeypatch):
    # A stream whose isatty() raises is treated as non-interactive (not a terminal).
    class _BadTTY:
        def isatty(self):
            raise ValueError("no isatty here")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(sys, "stdin", _BadTTY())
    monkeypatch.setattr(sys, "stdout", _BadTTY())
    assert wiz.should_offer_wizard(_FakeCtx(), interactive=False, assume_yes=False) is False


def test_offer_when_parameter_source_unavailable(monkeypatch):
    # If Click can't report parameter sources, fall back to offering the wizard.
    _interactive_terminal(monkeypatch)

    class _BrokenCtx:
        def get_parameter_source(self, name):
            raise RuntimeError("no source info")
    assert wiz.should_offer_wizard(_BrokenCtx(), interactive=False, assume_yes=False) is True


# ── run_deploy_wizard ───────────────────────────────────────────────────────--

def _config():
    return {"project": {"name": "demo-app"}}


def test_wizard_cancel_returns_none(monkeypatch):
    monkeypatch.setattr(wiz, "_ask", _queue(["cancel"]))
    assert wiz.run_deploy_wizard(_config(), DeployOptions()) is None


def test_wizard_default_path_returns_defaults(monkeypatch):
    monkeypatch.setattr(wiz, "_ask", _queue(["default"]))
    monkeypatch.setattr(wiz, "_confirm", _queue([True]))  # confirm_plan → proceed
    opts = wiz.run_deploy_wizard(_config(), DeployOptions())
    assert opts is not None
    assert opts.env == "local"
    assert opts.skip_scan is False
    assert opts.use_gitops is False


def test_wizard_default_path_declined_returns_none(monkeypatch):
    monkeypatch.setattr(wiz, "_ask", _queue(["default"]))
    monkeypatch.setattr(wiz, "_confirm", _queue([False]))  # decline at confirm_plan
    assert wiz.run_deploy_wizard(_config(), DeployOptions()) is None


def test_wizard_custom_prod_gitops(monkeypatch):
    # _ask order: mode, env, fail_on, slot, gitops_branch
    monkeypatch.setattr(wiz, "_ask", _queue(["custom", "prod", "CRITICAL", "none", "main"]))
    # _confirm order: build, run_scans, trivy, sonarqube, dast, gitops, replicas?, proceed
    monkeypatch.setattr(
        wiz, "_confirm",
        _queue([True, True, True, False, True, True, False, True]),
    )
    opts = wiz.run_deploy_wizard(_config(), DeployOptions())
    assert opts is not None
    assert opts.env == "prod"
    assert opts.skip_build is False
    assert opts.skip_scan is False
    assert opts.skip_trivy is False
    assert opts.skip_sonarqube is True
    assert opts.fail_on == "CRITICAL"
    assert opts.skip_dast is False
    assert opts.slot is None
    assert opts.use_gitops is True
    assert opts.gitops_branch == "main"
    assert opts.replicas is None


def test_wizard_custom_skips_scans(monkeypatch):
    # local env, no build skip, decline scans → skip_scan True, no scan sub-prompts
    monkeypatch.setattr(wiz, "_ask", _queue(["custom", "local"]))
    # _confirm: build=True, run_scans=False, replicas-override=False, proceed=True
    monkeypatch.setattr(wiz, "_confirm", _queue([True, False, False, True]))
    opts = wiz.run_deploy_wizard(_config(), DeployOptions())
    assert opts is not None
    assert opts.env == "local"
    assert opts.skip_scan is True


def test_wizard_custom_replica_override(monkeypatch):
    monkeypatch.setattr(wiz, "_ask", _queue(["custom", "local"]))
    # build=True, run_scans=False, replicas-override=True, proceed=True
    monkeypatch.setattr(wiz, "_confirm", _queue([True, False, True, True]))
    monkeypatch.setattr(wiz, "_ask_int", _queue([5]))
    opts = wiz.run_deploy_wizard(_config(), DeployOptions())
    assert opts is not None
    assert opts.replicas == 5


# ── to_cli_args / equivalent_command ───────────────────────────────────────--

def test_equivalent_command_defaults_has_no_flags():
    assert wiz.equivalent_command(DeployOptions()) == "guardops deploy"


def test_equivalent_command_prod_skip_dast():
    opts = DeployOptions(env="prod", skip_dast=True)
    assert wiz.equivalent_command(opts) == "guardops deploy --env prod --skip-dast"


def test_to_cli_args_gitops_branch_and_replicas():
    opts = DeployOptions(env="staging", use_gitops=True, gitops_branch="release", replicas=3)
    args = wiz.to_cli_args(opts)
    assert "--gitops" in args
    assert args[args.index("--gitops-branch") + 1] == "release"
    assert args[args.index("--replicas") + 1] == "3"


def test_to_cli_args_fail_on_only_when_non_default():
    assert "--fail-on" not in wiz.to_cli_args(DeployOptions(fail_on="HIGH"))
    assert "--fail-on" in wiz.to_cli_args(DeployOptions(fail_on="CRITICAL"))


def test_to_cli_args_slot_build_trivy():
    opts = DeployOptions(slot="blue", skip_build=True, skip_trivy=True)
    args = wiz.to_cli_args(opts)
    assert args[args.index("--slot") + 1] == "blue"
    assert "--skip-build" in args
    assert "--skip-trivy" in args


def test_confirm_plan_with_slot(monkeypatch):
    monkeypatch.setattr(wiz, "_confirm", lambda *a, **k: True)
    opts = DeployOptions(env="staging", slot="green")
    assert wiz.confirm_plan(opts) is True


# ── deploy_command integration ──────────────────────────────────────────────--

def test_bare_deploy_non_tty_does_not_prompt(runner):
    """Under CliRunner stdin is not a TTY → wizard must be skipped (CI-safe)."""
    with patch("cli.commands.deploy_cmd._execute_deploy") as mock_exec, \
         patch("cli.commands.deploy_cmd.run_deploy_wizard") as mock_wiz, \
         patch("cli.commands.deploy_cmd.load_config", return_value=_config()):
        result = runner.invoke(deploy_command, [])
    assert result.exit_code == 0
    mock_wiz.assert_not_called()
    mock_exec.assert_called_once()


def test_interactive_flag_forces_wizard(runner):
    with patch("cli.commands.deploy_cmd._execute_deploy") as mock_exec, \
         patch("cli.commands.deploy_cmd.run_deploy_wizard",
               return_value=DeployOptions(env="staging")) as mock_wiz, \
         patch("cli.commands.deploy_cmd.load_config", return_value=_config()):
        result = runner.invoke(deploy_command, ["-i"])
    assert result.exit_code == 0
    mock_wiz.assert_called_once()
    mock_exec.assert_called_once()
    # the resolved options from the wizard are what gets executed
    assert mock_exec.call_args.args[0].env == "staging"


def test_wizard_cancel_exits_without_deploy(runner):
    with patch("cli.commands.deploy_cmd._execute_deploy") as mock_exec, \
         patch("cli.commands.deploy_cmd.run_deploy_wizard", return_value=None), \
         patch("cli.commands.deploy_cmd.load_config", return_value=_config()):
        result = runner.invoke(deploy_command, ["-i"])
    assert result.exit_code == 0
    mock_exec.assert_not_called()

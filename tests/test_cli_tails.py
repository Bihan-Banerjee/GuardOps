"""
tests/test_cli_tails.py — assorted uncovered branches across small CLI/runner modules.

status --watch, logs --previous + Ctrl-C, admission empty-dir / apply-failure, the
sbom package counter's SPDX fallback, output helpers, and init scaffolding branches.
"""

from pathlib import Path
from unittest.mock import patch

from cli.commands.status_cmd import status_command
from cli.commands.logs_cmd import logs_command
from cli.commands.admission_cmd import admission_command
from cli.commands.init_cmd import init_command
from backend.security import sbom_runner as sr
from cli.utils import output


def _raise_ki(*a, **k):
    raise KeyboardInterrupt()


# ── status --watch ────────────────────────────────────────────────────────────

def test_status_watch_breaks_on_interrupt(runner, monkeypatch):
    monkeypatch.setattr("cli.commands.status_cmd.load_config",
                        lambda: {"project": {"name": "x"}, "kubernetes": {"namespace": "default"}})
    monkeypatch.setattr("cli.commands.status_cmd.get_project_name", lambda c: "x")
    monkeypatch.setattr("cli.commands.status_cmd._print_status", lambda *a: None)
    monkeypatch.setattr("time.sleep", _raise_ki)
    r = runner.invoke(status_command, ["--watch"])
    assert r.exit_code == 0 and "Stopped watching" in r.output


# ── logs --previous + interrupt ───────────────────────────────────────────────

def _logs_cfg(monkeypatch):
    monkeypatch.setattr("cli.commands.logs_cmd.load_config",
                        lambda: {"project": {"name": "x"}, "kubernetes": {"namespace": "default"}})
    monkeypatch.setattr("cli.commands.logs_cmd.get_project_name", lambda c: "x")


def test_logs_previous_flag(runner, monkeypatch):
    _logs_cfg(monkeypatch)
    with patch("cli.commands.logs_cmd.subprocess.run") as mk:
        r = runner.invoke(logs_command, ["--pod", "p", "--previous"])
    assert r.exit_code == 0 and "--previous" in mk.call_args[0][0]


def test_logs_keyboard_interrupt(runner, monkeypatch):
    _logs_cfg(monkeypatch)
    with patch("cli.commands.logs_cmd.subprocess.run", side_effect=KeyboardInterrupt):
        r = runner.invoke(logs_command, ["--pod", "p"])
    assert r.exit_code == 0 and "stopped" in r.output.lower()


# ── admission empty-dir / apply-failure ───────────────────────────────────────

def test_admission_empty_after_skipping_networkpolicy(runner, tmp_path):
    (tmp_path / "networkpolicy-egress.yaml").write_text("kind: NetworkPolicy\n", encoding="utf-8")
    r = runner.invoke(admission_command, ["--policy-dir", str(tmp_path)])
    assert r.exit_code == 1 and "No .yaml policies" in r.output


def test_admission_apply_failure(runner, tmp_path):
    (tmp_path / "p.yaml").write_text("validationFailureAction: __POLICY_ACTION__\n", encoding="utf-8")
    with patch("cli.commands.admission_cmd.subprocess.run") as mk:
        mk.return_value.returncode = 1
        mk.return_value.stderr = "apply denied"
        mk.return_value.stdout = ""
        r = runner.invoke(admission_command, ["--policy-dir", str(tmp_path)])
    assert r.exit_code == 1 and "Failed to apply" in r.output


# ── sbom package counter ──────────────────────────────────────────────────────

def test_count_packages_spdx_fallback(tmp_path):
    spdx = tmp_path / "sbom.spdx.json"
    spdx.write_text('{"packages":[{"name":"a"},{"name":"b"},{"name":"c"}]}', encoding="utf-8")
    assert sr._count_packages({"spdx-json": str(spdx)}) == 3


def test_count_packages_missing_file_is_zero():
    assert sr._count_packages({"cyclonedx-json": "/nope/missing.json"}) == 0


# ── output helpers ────────────────────────────────────────────────────────────

def test_output_helpers_run():
    output.step(1, 5, "doing a thing")
    output.blank()
    output.section("A Section")
    output.success("ok")
    output.info("info")
    output.warn("warn")
    output.error("err")


# ── init scaffolding branches ─────────────────────────────────────────────────

def test_init_prompts_for_name_and_no_context(runner):
    with runner.isolated_filesystem():
        with patch("cli.commands.init_cmd.check_all_dependencies", return_value=True), \
             patch("cli.commands.init_cmd.get_command_output", return_value=""):   # no kubectl context
            r = runner.invoke(init_command, ["--cloud", "local"], input="myproject\n")
        assert r.exit_code == 0 and Path(".guardops.yaml").exists()


def test_init_skips_existing_dockerfile(runner):
    with runner.isolated_filesystem():
        Path("Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        with patch("cli.commands.init_cmd.check_all_dependencies", return_value=True), \
             patch("cli.commands.init_cmd.get_command_output", return_value="k3d-x"):
            r = runner.invoke(init_command, ["--name", "demo", "--force"])
        assert r.exit_code == 0
        assert Path("Dockerfile").read_text(encoding="utf-8") == "FROM scratch\n"   # untouched


def test_init_appends_to_existing_gitignore(runner):
    with runner.isolated_filesystem():
        Path(".gitignore").write_text("node_modules\n", encoding="utf-8")
        with patch("cli.commands.init_cmd.check_all_dependencies", return_value=True), \
             patch("cli.commands.init_cmd.get_command_output", return_value="k3d-x"):
            r = runner.invoke(init_command, ["--name", "demo"])
        assert r.exit_code == 0 and ".env" in Path(".gitignore").read_text(encoding="utf-8")

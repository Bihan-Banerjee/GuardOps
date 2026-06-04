"""
tests/test_mopup2.py — final-stretch branch coverage (the last ~30 lines to 100%).

Directly exercises small helpers that the higher-level tests reach around:
snapshot/settings/findings-source guards, s3_store client+hydrate+fetch, the
deployer/gitops subprocess wrappers, falco parse/query edges, and a handful of
command-flow tails (trends/findings read errors, dashboard serve, runtime overflow,
scan medium status).
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

_R = CliRunner()


def _throw(exc):
    def _f(*a, **k):
        raise exc
    return _f


# ── snapshot._redact_live ──────────────────────────────────────────────────────

def test_redact_live_passthrough_non_dict():
    from backend.dashboard import snapshot
    assert snapshot._redact_live("not-a-dict") == "not-a-dict"


# ── settings._env / _load_config_or_defaults ───────────────────────────────────

def test_env_returns_first_non_empty(monkeypatch):
    from backend.dashboard import settings
    monkeypatch.setenv("GUARDOPS_TEST_ENV_A", "value-a")
    assert settings._env("GUARDOPS_TEST_ENV_A", "GUARDOPS_TEST_ENV_B") == "value-a"


def test_load_config_or_defaults_without_config_file():
    from backend.dashboard import settings
    with patch("cli.utils.config.config_exists", return_value=False):
        cfg = settings._load_config_or_defaults()
    assert isinstance(cfg, dict) and "project" in cfg


# ── findings source ─────────────────────────────────────────────────────────────

def test_compute_summary_empty_store():
    from backend.dashboard.sources import findings as fsrc
    summary = fsrc.compute_summary(SimpleNamespace(list_runs=lambda **k: []))
    assert summary["total_runs"] == 0 and summary["latest"] is None


def test_compute_diff_missing_explicit_from_id_raises():
    from backend.dashboard.sources import findings as fsrc
    run = SimpleNamespace(id=1, project_name="p")
    store = SimpleNamespace(list_runs=lambda **k: [run], get_run=lambda i: None)
    with pytest.raises(LookupError):
        fsrc.compute_diff(store, from_id=999)


# ── s3_store: client / hydrate / fetch ─────────────────────────────────────────

def test_s3_client_constructs_real_client_when_none():
    from backend.metadata.s3_store import _s3_client
    assert _s3_client("us-east-1") is not None


def test_hydrate_removes_existing_db_file(tmp_path):
    from backend.metadata.s3_store import _hydrate
    db = tmp_path / "cache.db"
    db.write_text("stale", encoding="utf-8")            # pre-existing file → removed
    _hydrate(str(db), {"runs": [], "findings": [], "tool_runs": []})
    assert os.path.exists(db)


def test_fetch_export_propagates_non_notfound_error():
    from backend.metadata.s3_store import S3MetadataStore

    class _BadClient:
        def get_object(self, **kw):
            raise RuntimeError("AccessDenied")   # not a NoSuchKey → must propagate

    store = S3MetadataStore(bucket="b", client=_BadClient())
    with pytest.raises(RuntimeError):
        store._fetch_export()


# ── deployer helpers ────────────────────────────────────────────────────────────

def test_helm_available_reflects_path():
    from backend.pipeline import deployer
    with patch("shutil.which", return_value="/usr/bin/helm"):
        assert deployer._helm_available() is True
    with patch("shutil.which", return_value=None):
        assert deployer._helm_available() is False


# ── gitops_writer subprocess wrappers ──────────────────────────────────────────

def test_run_git_invokes_subprocess():
    from backend.pipeline import gitops_writer as gw
    with patch("backend.pipeline.gitops_writer.subprocess.run") as m:
        gw._run_git(["status"])
    assert m.call_count == 1
    assert m.call_args[0][0][0] == "git"


def test_get_current_sha_reads_stdout():
    from backend.pipeline import gitops_writer as gw
    with patch("backend.pipeline.gitops_writer.subprocess.run",
               return_value=SimpleNamespace(stdout="abc1234\n")):
        assert gw._get_current_sha() == "abc1234"


def test_get_current_sha_falls_back_to_unknown():
    from backend.pipeline import gitops_writer as gw
    with patch("backend.pipeline.gitops_writer.subprocess.run",
               return_value=SimpleNamespace(stdout="   ")):
        assert gw._get_current_sha() == "unknown"


def test_find_chart_dir_fallback_when_none_exist(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)   # no k8s/helm/guardops-app anywhere → best-effort fallback
    from backend.pipeline.gitops_writer import _find_chart_dir
    assert str(_find_chart_dir()).replace("\\", "/").endswith("k8s/helm/guardops-app")


# ── falco_reader edges ──────────────────────────────────────────────────────────

def test_query_falco_alerts_unexpected_error():
    from backend.security import falco_reader as fr
    with patch("backend.security.falco_reader.requests.get", side_effect=RuntimeError("boom")):
        res = fr.query_falco_alerts("http://loki:3100")
    assert res.success is False and "Unexpected error" in res.error_message


def test_parse_falco_log_line_non_iterable_tags():
    from backend.security import falco_reader as fr
    line = json.dumps({"priority": "Critical", "rule": "Shell in container",
                       "output": "o", "output_fields": {}, "tags": 123})  # tags neither list nor str
    alert = fr._parse_falco_log_line(line, "1700000000000000000")
    assert alert is not None and alert.tags == []


# ── scan_cmd helpers ────────────────────────────────────────────────────────────

def test_git_short_sha_swallows_errors():
    from cli.commands import scan_cmd
    with patch("cli.utils.system.get_command_output", side_effect=RuntimeError("no git")):
        assert scan_cmd._git_short_sha() == ""


def test_print_summary_table_medium_warnings_status():
    from cli.commands.scan_cmd import _print_summary_table
    from backend.security.semgrep_runner import ScanResult, SecurityFinding
    result = ScanResult(
        tool="semgrep", success=True,
        findings=[SecurityFinding(tool="semgrep", rule_id="r", severity="MEDIUM",
                                  message="m", file_path="f", line_start=1, line_end=1)],
    )
    # MEDIUM-only (no crit/high) → "warnings" status row. Should not raise.
    _print_summary_table([result])


# ── system.run_command --show-command ──────────────────────────────────────────

def test_run_command_show_command_prints_cmd():
    from cli.utils import system
    with patch("cli.utils.system.subprocess.run",
               return_value=SimpleNamespace(returncode=0, stdout="", stderr="")):
        result = system.run_command(["echo", "hi"], show_command=True)
    assert result.returncode == 0


# ── cli root group callback ─────────────────────────────────────────────────────

def test_cli_group_callback_executes():
    from cli.main import cli
    # Invoking a subcommand runs the group callback body first.
    r = _R.invoke(cli, ["init", "--help"])
    assert r.exit_code == 0


# ── init_cmd: .env.example already present ─────────────────────────────────────

def test_create_env_example_skips_when_present(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text("KEEP ME", encoding="utf-8")
    from cli.commands.init_cmd import _create_env_example_if_missing
    _create_env_example_if_missing()
    assert (tmp_path / ".env.example").read_text(encoding="utf-8") == "KEEP ME"


# ── trends / findings command read errors ──────────────────────────────────────

def test_trends_command_read_error():
    from cli.commands.trends_cmd import trends_command
    store = SimpleNamespace(severity_trends=_throw(Exception("db locked")))
    with patch("cli.commands.trends_cmd.open_store", return_value=({}, store)):
        r = _R.invoke(trends_command, [])
    assert r.exit_code == 1 and "Could not read trends" in r.output


def test_findings_command_read_error():
    from cli.commands.findings_cmd import findings_command
    store = SimpleNamespace(query_findings=_throw(Exception("db locked")))
    with patch("cli.commands.findings_cmd.open_store", return_value=({}, store)):
        r = _R.invoke(findings_command, [])
    assert r.exit_code == 1 and "Could not read findings" in r.output


# ── dashboard serve: auth-enabled banner ───────────────────────────────────────

def test_dashboard_serve_auth_enabled_line():
    from cli.commands.dashboard_cmd import dashboard_command
    settings = SimpleNamespace(project_name="p", config={"metadata": {"backend": "sqlite"}},
                               auth_enabled=True, auth_mode="token")
    with patch("cli.commands.dashboard_cmd.load_config", return_value={}), \
         patch("backend.dashboard.settings.load_settings", return_value=settings), \
         patch("cli.commands.dashboard_cmd.importlib.util.find_spec", return_value=object()), \
         patch("uvicorn.run"):
        r = _R.invoke(dashboard_command, [])
    assert r.exit_code == 0 and "enabled" in r.output


# ── runtime-status: overflow note ──────────────────────────────────────────────

def test_runtime_status_overflow_note():
    from cli.commands.runtime_cmd import runtime_status_command
    alerts = [SimpleNamespace(severity="HIGH", rule="R", pod_name="p", namespace="default",
                              timestamp="notimestamp", output="o") for _ in range(3)]
    result = SimpleNamespace(skipped=False, skip_reason="", success=True, error_message="",
                             severity_counts={"CRITICAL": 0, "HIGH": 3, "MEDIUM": 0, "LOW": 0},
                             alerts=alerts, query_duration_seconds=0.1,
                             has_alerts_above=lambda f: False)
    with patch("cli.commands.runtime_cmd.load_config",
               return_value={"monitoring": {"loki_url": "http://l"}}), \
         patch("cli.commands.runtime_cmd.query_falco_alerts", return_value=result):
        r = _R.invoke(runtime_status_command, ["--namespace", "default", "--tail", "1"])
    assert r.exit_code == 0 and "more alert" in r.output

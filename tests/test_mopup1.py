"""
tests/test_mopup1.py — assorted final-stretch branch coverage.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

_R = CliRunner()


def _raise(*a, **k):
    raise Exception("boom")


# ── trivy_runner fs branches ──────────────────────────────────────────────────

def test_trivy_fs_timeout_empty_badjson(monkeypatch, tmp_path):
    import subprocess
    from backend.security import trivy_runner as tr
    monkeypatch.setattr(tr.shutil, "which", lambda c: "/usr/bin/trivy")

    monkeypatch.setattr(tr.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("trivy", 1)))
    assert tr.run_trivy_filesystem(str(tmp_path), {}).success is False

    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: MagicMock(stdout="   "))
    assert tr.run_trivy_filesystem(str(tmp_path), {}).findings == []

    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: MagicMock(stdout="not json"))
    assert tr.run_trivy_filesystem(str(tmp_path), {}).findings == []


# ── runtime-status title parts + table truncation ─────────────────────────────

def test_runtime_title_and_table(monkeypatch):
    from cli.commands.runtime_cmd import runtime_status_command
    alert = SimpleNamespace(severity="CRITICAL", rule="R" * 60, pod_name="p" * 40,
                            namespace="default", timestamp="notimestamp", output="bad")
    result = SimpleNamespace(skipped=False, skip_reason="", success=True, error_message="",
                             severity_counts={"CRITICAL": 1, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
                             alerts=[alert], query_duration_seconds=0.4,
                             has_alerts_above=lambda f: False)
    with patch("cli.commands.runtime_cmd.load_config", return_value={"monitoring": {"loki_url": "http://l"}}), \
         patch("cli.commands.runtime_cmd.query_falco_alerts", return_value=result):
        r = _R.invoke(runtime_status_command, ["--namespace", "default", "--severity", "HIGH"])
    assert r.exit_code == 0


# ── scan sonar gate passed ────────────────────────────────────────────────────

def test_scan_sonar_gate_passed(monkeypatch):
    from cli.commands.scan_cmd import scan_command
    from backend.security.semgrep_runner import ScanResult
    P = "cli.commands.scan_cmd."
    monkeypatch.setattr(P + "load_config", lambda: {"security": {}})
    monkeypatch.setattr(P + "get_project_name", lambda c: "demo")
    monkeypatch.setattr(P + "_git_short_sha", lambda: "abc")
    for fn in ("run_semgrep", "run_bandit", "run_trivy_filesystem", "run_trivy_image"):
        monkeypatch.setattr(P + fn, lambda *a: ScanResult(tool="t", success=True, findings=[]))
    monkeypatch.setattr(P + "run_sonarqube", lambda *a: ScanResult(tool="sonarqube", success=True, findings=[]))
    monkeypatch.setattr(P + "check_quality_gate", lambda c: (True, "all good"))
    monkeypatch.setattr(P + "generate_report",
                        lambda **k: SimpleNamespace(blocked=False,
                                                    severity_counts={"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}))
    monkeypatch.setattr(P + "persist_report_safe", lambda *a, **k: None)
    r = _R.invoke(scan_command, [])
    assert r.exit_code == 0 and "Quality gate" in r.output


# ── status partial-ready ──────────────────────────────────────────────────────

def test_status_partial_ready(runner, monkeypatch):
    from cli.commands.status_cmd import status_command
    status = {"name": "x", "namespace": "default", "desired_replicas": 3,
              "ready_replicas": 1, "available_replicas": 1}
    monkeypatch.setattr("cli.commands.status_cmd.load_config",
                        lambda: {"project": {"name": "x"}, "kubernetes": {"namespace": "default"}})
    monkeypatch.setattr("cli.commands.status_cmd.get_project_name", lambda c: "x")
    monkeypatch.setattr("cli.commands.status_cmd.get_deployment_status", lambda p, n: status)
    monkeypatch.setattr("cli.commands.status_cmd.get_pods", lambda p, n: [])
    r = runner.invoke(status_command, [])
    assert r.exit_code == 0


# ── switch apply-stdin timeout/exception ──────────────────────────────────────

def test_switch_apply_stdin_timeout_and_error(monkeypatch):
    import subprocess
    from cli.commands import switch_cmd as sc
    monkeypatch.setattr(sc.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("kubectl", 1)))
    assert sc._kubectl_apply_stdin("y")[0] is False
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert sc._kubectl_apply_stdin("y")[0] is False


# ── sbom spdx-parse exception ─────────────────────────────────────────────────

def test_sbom_count_spdx_unparseable(tmp_path):
    from backend.security import sbom_runner as sr
    bad = tmp_path / "sbom.spdx.json"
    bad.write_text("not json", encoding="utf-8")
    assert sr._count_packages({"spdx-json": str(bad)}) == 0


# ── cosign verify-attestation cosign-missing ──────────────────────────────────

def test_cosign_attestation_missing(monkeypatch):
    from backend.security import cosign_verifier as cv
    monkeypatch.setattr(cv.shutil, "which", lambda c: None)
    assert cv.verify_attestation("ref").skipped is True


# ── doctor with dashboard extra missing ───────────────────────────────────────

def test_doctor_dashboard_extra_missing(runner):
    real = __import__("importlib").util.find_spec

    def fake(name, *a, **k):
        if name in ("uvicorn", "fastapi"):
            return None
        return real(name, *a, **k)
    with patch("cli.commands.doctor_cmd.shutil.which", side_effect=lambda c: f"/usr/bin/{c}"), \
         patch("cli.commands.doctor_cmd.config_exists", return_value=True), \
         patch("cli.commands.doctor_cmd.importlib.util.find_spec", side_effect=fake):
        r = runner.invoke(__import__("cli.commands.doctor_cmd", fromlist=["doctor_command"]).doctor_command, [])
    assert r.exit_code == 0 and "Dashboard extra not installed" in r.output


# ── verify-image attestation skipped ──────────────────────────────────────────

def test_verify_attestation_skipped(runner):
    from cli.commands.verify_cmd import verify_image_command
    ok = SimpleNamespace(success=True, skipped=False, skip_reason="", error_message="")
    skipped = SimpleNamespace(success=False, skipped=True, skip_reason="cosign missing", error_message="")
    with patch("cli.commands.verify_cmd.verify_image", return_value=ok), \
         patch("cli.commands.verify_cmd.verify_attestation", return_value=skipped):
        r = runner.invoke(verify_image_command, ["ref@sha256:x", "--attestation"])
    assert r.exit_code == 0


# ── history read error ────────────────────────────────────────────────────────

def test_history_read_error():
    from cli.commands.history_cmd import history_command
    store = SimpleNamespace(list_runs=_raise)
    with patch("cli.commands.history_cmd.open_store", return_value=({}, store)):
        r = _R.invoke(history_command, [])
    assert r.exit_code == 1 and "Could not read scan history" in r.output

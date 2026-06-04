"""
tests/test_deploy_cmd.py — `guardops deploy` orchestration.

Exercises the build → scan → push → deploy → DAST pipeline through deploy_command:
local success, the --gitops/local guard, build/scan/push/deploy failure gates, and
the prod ECR-push path. Every collaborator (build/scan/push/deploy/DAST/config) is
mocked — nothing builds, scans, pushes, or deploys for real.
"""

from types import SimpleNamespace

import pytest

from cli.commands.deploy_cmd import deploy_command, _run_dast_step, _print_dast_summary
from cli.commands._deploy_wizard import DeployOptions
from backend.security.zap_runner import ZapScanResult, ZapFinding

_P = "cli.commands.deploy_cmd."


def _cfg():
    return {"project": {"name": "test-app"}, "kubernetes": {"namespace": "default"},
            "security": {"tools": {}}, "docker": {}}


def _prod_cfg():
    return {"project": {"name": "test-app"}, "kubernetes": {"namespace": "default"},
            "security": {"tools": {"owasp_zap": True}, "zap_target_url": ""}, "docker": {}}


def _zap_finding(sev="CRITICAL"):
    return ZapFinding(severity=sev, alert="Reflected XSS", description="d", solution="s",
                      url="http://x", evidence="", confidence="High", zap_riskcode=3, instance_count=2)


def _build_ok(**k):
    return SimpleNamespace(success=True, full_image_ref="test-app:abc123",
                           build_duration_seconds=1.0, error_message="")


def _deploy_ok(**k):
    return SimpleNamespace(success=True, helm_release="test-app", helm_revision=1,
                           service_url="http://test-app.local", error_message="")


def _push_ok(*a, **k):
    return SimpleNamespace(success=True, error_message="",
                           image_uri="123.dkr.ecr.ap-south-1.amazonaws.com/test-app:abc123")


def _wire(monkeypatch, cfg=None):
    """Patch every deploy collaborator to a success default."""
    cfg = cfg or _cfg()
    s = monkeypatch.setattr
    s(_P + "load_config", lambda: cfg)
    s(_P + "should_offer_wizard", lambda *a: False)          # never prompt
    s(_P + "get_env_config", lambda c, e: c)
    s(_P + "resolve_namespace", lambda c, e: "default")
    s(_P + "resolve_image_tag", lambda sha, e, c: "abc123")
    s(_P + "resolve_helm_release_name", lambda *a: "test-app")
    s("cli.utils.system.get_command_output", lambda *a, **k: "abc123")   # git sha (imported in-func)
    s(_P + "build_image", _build_ok)
    s(_P + "run_semgrep", lambda *a: SimpleNamespace(tool="semgrep", success=True))
    s(_P + "run_bandit", lambda *a: SimpleNamespace(tool="bandit", success=True))
    s(_P + "run_trivy_filesystem", lambda *a: SimpleNamespace(tool="trivy-fs", success=True))
    s(_P + "run_trivy_image", lambda *a: SimpleNamespace(tool="trivy", success=True))
    s(_P + "run_sonarqube", lambda *a: SimpleNamespace(tool="sonarqube", success=True))
    s(_P + "generate_report",
      lambda **k: SimpleNamespace(blocked=False,
                                  severity_counts={"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}))
    s(_P + "persist_report_safe", lambda *a, **k: None)
    s(_P + "push_to_ecr", _push_ok)
    s(_P + "import_image_to_k3d", lambda *a, **k: True)
    s(_P + "deploy_helm", _deploy_ok)
    # DAST + GitOps collaborators (only exercised on the prod/--gitops paths)
    s(_P + "run_zap_baseline", lambda **k: ZapScanResult(skipped=True, skip_reason="default"))
    s(_P + "rollback_helm",
      lambda **k: SimpleNamespace(success=True, rolled_back_to=1, error_message=""))
    s(_P + "_sanitize_release_name", lambda n: n)
    s(_P + "write_image_override", lambda **k: "values-override-prod.yaml")
    s(_P + "commit_and_push",
      lambda **k: SimpleNamespace(success=True, error_message="", skipped=False, commit_sha="def4567"))
    s(_P + "trigger_argocd_sync", lambda **k: SimpleNamespace(success=True, error_message=""))
    s(_P + "get_argocd_url", lambda c: "https://argo.example")
    s(_P + "get_argocd_app_name", lambda c, e: "guardops-app-prod")
    s(_P + "get_argocd_token_env_var", lambda c: "ARGOCD_TOKEN")
    return cfg


def test_local_deploy_success(runner, monkeypatch):
    _wire(monkeypatch)
    r = runner.invoke(deploy_command, ["--env", "local", "--skip-scan", "--skip-build"])
    assert r.exit_code == 0, r.output
    assert "Deployment complete" in r.output


def test_gitops_local_is_rejected(runner, monkeypatch):
    _wire(monkeypatch)
    r = runner.invoke(deploy_command, ["--env", "local", "--gitops"])
    assert r.exit_code == 1
    assert "gitops is only supported" in r.output.lower()


def test_build_failure_exits(runner, monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(_P + "build_image",
                        lambda **k: SimpleNamespace(success=False, error_message="docker daemon down"))
    r = runner.invoke(deploy_command, ["--env", "local", "--skip-scan"])
    assert r.exit_code == 1
    assert "Build failed" in r.output


def test_scan_blocked_exits(runner, monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(_P + "generate_report", lambda **k: SimpleNamespace(
        blocked=True, severity_counts={"CRITICAL": 1, "HIGH": 0, "MEDIUM": 0, "LOW": 0}))
    r = runner.invoke(deploy_command, ["--env", "local", "--skip-build"])
    assert r.exit_code == 1
    assert "blocked" in r.output.lower()


def test_prod_pushes_to_ecr(runner, monkeypatch):
    _wire(monkeypatch)
    r = runner.invoke(deploy_command, ["--env", "prod", "--skip-scan", "--skip-build", "--skip-dast"])
    assert r.exit_code == 0, r.output
    assert "Pushed to ECR" in r.output


def test_ecr_push_failure_exits(runner, monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(_P + "push_to_ecr",
                        lambda *a, **k: SimpleNamespace(success=False, error_message="access denied"))
    r = runner.invoke(deploy_command, ["--env", "prod", "--skip-scan", "--skip-build"])
    assert r.exit_code == 1
    assert "ECR push failed" in r.output


def test_helm_deploy_failure_exits(runner, monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(_P + "deploy_helm",
                        lambda **k: SimpleNamespace(success=False, error_message="helm timeout",
                                                    helm_release="", helm_revision=0, service_url=""))
    r = runner.invoke(deploy_command, ["--env", "local", "--skip-scan", "--skip-build"])
    assert r.exit_code == 1
    assert "Deployment failed" in r.output


def test_scan_pass_path(runner, monkeypatch):
    # not --skip-scan: runs the scanners, persists, prints the summary, passes the gate
    _wire(monkeypatch)
    r = runner.invoke(deploy_command, ["--env", "local", "--skip-build"])
    assert r.exit_code == 0, r.output
    assert "Security scans passed" in r.output


def test_dast_passes(runner, monkeypatch):
    _wire(monkeypatch, cfg=_prod_cfg())
    monkeypatch.setattr(_P + "run_zap_baseline",
                        lambda **k: ZapScanResult(success=True, skipped=False, findings=[], blocked=False))
    r = runner.invoke(deploy_command, ["--env", "prod", "--skip-scan", "--skip-build"])
    assert r.exit_code == 0, r.output
    assert "DAST passed" in r.output


def test_dast_blocked_triggers_rollback(runner, monkeypatch):
    _wire(monkeypatch, cfg=_prod_cfg())
    monkeypatch.setattr(_P + "run_zap_baseline",
                        lambda **k: ZapScanResult(success=True, skipped=False,
                                                  findings=[_zap_finding("CRITICAL")], blocked=True))
    r = runner.invoke(deploy_command, ["--env", "prod", "--skip-scan", "--skip-build"])
    assert r.exit_code == 1
    assert "DAST gate FAILED" in r.output
    assert "Rolled back" in r.output


def test_dast_scan_failure_is_non_fatal(runner, monkeypatch):
    _wire(monkeypatch, cfg=_prod_cfg())
    monkeypatch.setattr(_P + "run_zap_baseline",
                        lambda **k: ZapScanResult(success=False, skipped=False, error_message="zap crashed"))
    r = runner.invoke(deploy_command, ["--env", "prod", "--skip-scan", "--skip-build"])
    assert r.exit_code == 0, r.output
    assert "ZAP scan failed" in r.output


def test_gitops_commit_and_sync(runner, monkeypatch):
    _wire(monkeypatch, cfg=_prod_cfg())
    monkeypatch.setenv("ARGOCD_TOKEN", "tok")
    r = runner.invoke(deploy_command, ["--env", "prod", "--gitops", "--skip-scan", "--skip-build", "--skip-dast"])
    assert r.exit_code == 0, r.output
    assert "GitOps image override" in r.output


def test_slot_deploy(runner, monkeypatch):
    _wire(monkeypatch)
    r = runner.invoke(deploy_command, ["--env", "staging", "--slot", "blue", "--skip-scan", "--skip-build", "--skip-dast"])
    assert r.exit_code == 0, r.output
    assert "blue" in r.output


def test_wizard_cancel(runner, monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(_P + "should_offer_wizard", lambda *a: True)
    monkeypatch.setattr(_P + "run_deploy_wizard", lambda c, o: None)   # user cancelled
    r = runner.invoke(deploy_command, [])
    assert r.exit_code == 0
    assert "cancelled" in r.output.lower()


def test_wizard_resolves_options(runner, monkeypatch):
    _wire(monkeypatch)
    resolved = DeployOptions(env="local", slot=None, use_gitops=False, gitops_branch="main",
                             skip_scan=True, skip_build=True, skip_sonarqube=False, skip_trivy=False,
                             skip_dast=True, fail_on="HIGH", replicas=None)
    monkeypatch.setattr(_P + "should_offer_wizard", lambda *a: True)
    monkeypatch.setattr(_P + "run_deploy_wizard", lambda c, o: resolved)
    r = runner.invoke(deploy_command, [])
    assert r.exit_code == 0, r.output
    assert "Deployment complete" in r.output


# ── main-flow branches (build / k3d import / gitops / argocd) ──────────────────

def test_build_runs_when_not_skipped(runner, monkeypatch):
    # No --skip-build → build_image runs and the "Built …" success line prints.
    _wire(monkeypatch)
    r = runner.invoke(deploy_command, ["--env", "local", "--skip-scan", "--skip-dast"])
    assert r.exit_code == 0, r.output
    assert "Built" in r.output


def test_k3d_import_failure_exits(runner, monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(_P + "import_image_to_k3d", lambda *a, **k: False)
    r = runner.invoke(deploy_command, ["--env", "local", "--skip-scan", "--skip-build"])
    assert r.exit_code == 1
    assert "import" in r.output.lower()


def test_gitops_commit_exception_is_warned(runner, monkeypatch):
    # commit_and_push raising is non-fatal — Helm already succeeded.
    _wire(monkeypatch, cfg=_prod_cfg())
    def _boom(**k):
        raise RuntimeError("git push rejected")
    monkeypatch.setattr(_P + "commit_and_push", _boom)
    r = runner.invoke(deploy_command,
                      ["--env", "prod", "--gitops", "--skip-scan", "--skip-build", "--skip-dast"])
    assert r.exit_code == 0, r.output
    assert "GitOps override commit failed" in r.output


def test_gitops_commit_returns_failure_is_warned(runner, monkeypatch):
    _wire(monkeypatch, cfg=_prod_cfg())
    monkeypatch.setattr(_P + "commit_and_push",
                        lambda **k: SimpleNamespace(success=False, error_message="diverged",
                                                    skipped=False, commit_sha=""))
    r = runner.invoke(deploy_command,
                      ["--env", "prod", "--gitops", "--skip-scan", "--skip-build", "--skip-dast"])
    assert r.exit_code == 0, r.output
    assert "GitOps commit failed" in r.output


def test_argocd_sync_failure_is_warned(runner, monkeypatch):
    _wire(monkeypatch, cfg=_prod_cfg())
    monkeypatch.setenv("ARGOCD_TOKEN", "tok")
    monkeypatch.setattr(_P + "trigger_argocd_sync",
                        lambda **k: SimpleNamespace(success=False, error_message="app not found"))
    r = runner.invoke(deploy_command,
                      ["--env", "prod", "--gitops", "--skip-scan", "--skip-build", "--skip-dast"])
    assert r.exit_code == 0, r.output
    assert "ArgoCD sync trigger failed" in r.output


def test_argocd_sync_skipped_without_token(runner, monkeypatch):
    # gitops commit succeeds but no ARGOCD_TOKEN exported → explicit sync is skipped.
    _wire(monkeypatch, cfg=_prod_cfg())
    monkeypatch.delenv("ARGOCD_TOKEN", raising=False)
    r = runner.invoke(deploy_command,
                      ["--env", "prod", "--gitops", "--skip-scan", "--skip-build", "--skip-dast"])
    assert r.exit_code == 0, r.output
    assert "skipping explicit sync" in r.output


# ── _run_dast_step / _print_dast_summary (called directly) ─────────────────────

def _dast_cfg(enabled=False, target=""):
    return {"security": {"tools": {"owasp_zap": enabled}, "zap_target_url": target,
                         "report_dir": "security/reports"}}


def test_dast_step_staging_disabled_is_expected():
    res = _run_dast_step(config=_dast_cfg(enabled=False), env="staging", skip_dast=False,
                         service_url="", project_name="p", namespace="default")
    assert res.skipped is True and "owasp_zap is false" in res.skip_reason


def test_dast_step_enabled_nonprod_is_skipped():
    res = _run_dast_step(config=_dast_cfg(enabled=True), env="staging", skip_dast=False,
                         service_url="", project_name="p", namespace="default")
    assert res.skipped is True and "only runs in prod" in res.skip_reason


def test_dast_step_no_target_url_is_skipped():
    res = _run_dast_step(config=_dast_cfg(enabled=True, target=""), env="prod", skip_dast=False,
                         service_url="", project_name="p", namespace="default")
    assert res.skipped is True and "No target URL" in res.skip_reason


def test_dast_step_blocked_rollback_also_fails(monkeypatch):
    monkeypatch.setattr(_P + "run_zap_baseline",
                        lambda **k: ZapScanResult(success=True, skipped=False,
                                                  findings=[_zap_finding("CRITICAL")], blocked=True))
    monkeypatch.setattr(_P + "rollback_helm",
                        lambda **k: SimpleNamespace(success=False, rolled_back_to=0,
                                                    error_message="no prior revision"))
    monkeypatch.setattr(_P + "_sanitize_release_name", lambda n: n)
    with pytest.raises(SystemExit) as exc:
        _run_dast_step(config=_dast_cfg(enabled=True, target="http://app"), env="prod",
                       skip_dast=False, service_url="http://app", project_name="p",
                       namespace="default", helm_release_name="p")
    assert exc.value.code == 1


def test_print_dast_summary_truncates_over_five():
    findings = [_zap_finding("HIGH") for _ in range(6)]
    zap = ZapScanResult(success=True, skipped=False, findings=findings)
    # Should not raise; exercises the ">5 findings" truncation line.
    _print_dast_summary(zap)

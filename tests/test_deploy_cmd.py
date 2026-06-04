"""
tests/test_deploy_cmd.py — `guardops deploy` orchestration.

Exercises the build → scan → push → deploy → DAST pipeline through deploy_command:
local success, the --gitops/local guard, build/scan/push/deploy failure gates, and
the prod ECR-push path. Every collaborator (build/scan/push/deploy/DAST/config) is
mocked — nothing builds, scans, pushes, or deploys for real.
"""

from types import SimpleNamespace

from cli.commands.deploy_cmd import deploy_command

_P = "cli.commands.deploy_cmd."


def _cfg():
    return {"project": {"name": "test-app"}, "kubernetes": {"namespace": "default"},
            "security": {"tools": {}}, "docker": {}}


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
    s(_P + "generate_report", lambda **k: SimpleNamespace(blocked=False))
    s(_P + "persist_report_safe", lambda *a, **k: None)
    s(_P + "_print_scan_summary", lambda *a: None)
    s(_P + "push_to_ecr", _push_ok)
    s(_P + "import_image_to_k3d", lambda *a, **k: True)
    s(_P + "deploy_helm", _deploy_ok)
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
    monkeypatch.setattr(_P + "generate_report", lambda **k: SimpleNamespace(blocked=True))
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

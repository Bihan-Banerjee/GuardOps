"""
tests/test_deployer_phase3.py

Tests for Phase 3 Helm deploy functions in backend/pipeline/deployer.py

Coverage:
  - deploy_helm: success, helm not installed, chart not found, helm failure
  - rollback_helm: success, failure, helm not installed
  - get_helm_history: success, empty, malformed JSON, helm failure
  - _split_image_ref: various formats including ECR URLs
  - _sanitize_release_name: special chars, length limit, edge cases
  - _find_chart_path: found, not found
  - _get_helm_revision: success, failure, malformed JSON
  - _get_ingress_url: success, failure fallback
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from backend.pipeline.deployer import (
    deploy_helm,
    rollback_helm,
    get_helm_history,
    _split_image_ref,
    _sanitize_release_name,
    _find_chart_path,
    _get_helm_revision,
    _get_ingress_url,
    DeployResult,
    RollbackResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(stdout=""):
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = ""
    return m


def _fail(stderr="error", returncode=1):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = ""
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# _split_image_ref
# ---------------------------------------------------------------------------

class TestSplitImageRef:
    def test_simple_name_and_tag(self):
        assert _split_image_ref("myapp:abc123") == ("myapp", "abc123")

    def test_no_tag_defaults_to_latest(self):
        assert _split_image_ref("myapp") == ("myapp", "latest")

    def test_ecr_url_with_tag(self):
        repo, tag = _split_image_ref(
            "123456789.dkr.ecr.us-east-1.amazonaws.com/guardops-app:abc123"
        )
        assert repo == "123456789.dkr.ecr.us-east-1.amazonaws.com/guardops-app"
        assert tag == "abc123"

    def test_ecr_url_without_tag(self):
        repo, tag = _split_image_ref(
            "123456789.dkr.ecr.us-east-1.amazonaws.com/guardops-app"
        )
        assert tag == "latest"

    def test_local_with_sha_tag(self):
        assert _split_image_ref("test-app:1249dda") == ("test-app", "1249dda")

    def test_latest_tag_explicit(self):
        assert _split_image_ref("myapp:latest") == ("myapp", "latest")


# ---------------------------------------------------------------------------
# _sanitize_release_name
# ---------------------------------------------------------------------------

class TestSanitizeReleaseName:
    def test_lowercase_passthrough(self):
        assert _sanitize_release_name("myapp") == "myapp"

    def test_uppercase_lowercased(self):
        assert _sanitize_release_name("MyApp") == "myapp"

    def test_underscores_to_hyphens(self):
        assert _sanitize_release_name("my_app") == "my-app"

    def test_spaces_to_hyphens(self):
        assert _sanitize_release_name("my app") == "my-app"

    def test_leading_trailing_hyphens_stripped(self):
        assert _sanitize_release_name("-myapp-") == "myapp"

    def test_too_long_truncated_at_53(self):
        long_name = "a" * 60
        result = _sanitize_release_name(long_name)
        assert len(result) <= 53

    def test_empty_returns_default(self):
        assert _sanitize_release_name("") == "guardops-app"

    def test_special_chars_removed(self):
        result = _sanitize_release_name("my.app@v1.0!")
        assert result == "my-app-v1-0"

    def test_valid_name_unchanged(self):
        assert _sanitize_release_name("guardops-app") == "guardops-app"


# ---------------------------------------------------------------------------
# _find_chart_path
# ---------------------------------------------------------------------------

class TestFindChartPath:
    def test_returns_none_when_no_chart_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert _find_chart_path() is None

    def test_finds_chart_in_k8s_helm_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        chart_dir = tmp_path / "k8s" / "helm" / "guardops-app"
        chart_dir.mkdir(parents=True)
        (chart_dir / "Chart.yaml").write_text("apiVersion: v2\nname: guardops-app\n")
        result = _find_chart_path()
        assert result is not None
        assert "guardops-app" in result

    def test_returns_string_not_path_object(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        chart_dir = tmp_path / "k8s" / "helm" / "guardops-app"
        chart_dir.mkdir(parents=True)
        (chart_dir / "Chart.yaml").write_text("apiVersion: v2\n")
        result = _find_chart_path()
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _get_helm_revision
# ---------------------------------------------------------------------------

class TestGetHelmRevision:
    def test_returns_revision_number(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok(json.dumps({"version": 3})))
        assert _get_helm_revision("myapp", "default") == 3

    def test_returns_zero_on_command_failure(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_fail())
        assert _get_helm_revision("myapp", "default") == 0

    def test_returns_zero_on_malformed_json(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok("not json"))
        assert _get_helm_revision("myapp", "default") == 0

    def test_returns_zero_when_version_key_missing(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok(json.dumps({"status": "deployed"})))
        assert _get_helm_revision("myapp", "default") == 0


# ---------------------------------------------------------------------------
# _get_ingress_url
# ---------------------------------------------------------------------------

class TestGetIngressUrl:
    def test_returns_http_url_from_host(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok("test-app.local"))
        url = _get_ingress_url("test-app", "default")
        assert url == "http://test-app.local"

    def test_returns_fallback_on_kubectl_failure(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_fail())
        url = _get_ingress_url("test-app", "default")
        assert "localhost" in url or "port-forward" in url

    def test_returns_fallback_on_empty_output(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok(""))
        url = _get_ingress_url("test-app", "default")
        assert "port-forward" in url or "localhost" in url


# ---------------------------------------------------------------------------
# get_helm_history
# ---------------------------------------------------------------------------

class TestGetHelmHistory:
    def test_returns_list_of_revisions(self, mocker):
        history = [
            {"revision": 1, "status": "superseded", "chart": "guardops-app-0.3.0",
             "description": "Install complete", "updated": "2026-01-01"},
            {"revision": 2, "status": "deployed", "chart": "guardops-app-0.3.0",
             "description": "Upgrade complete", "updated": "2026-01-02"},
        ]
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok(json.dumps(history)))
        result = get_helm_history("myapp")
        assert len(result) == 2
        assert result[1]["status"] == "deployed"
        assert result[0]["revision"] == 1

    def test_returns_empty_on_command_failure(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_fail())
        assert get_helm_history("myapp") == []

    def test_returns_empty_on_malformed_json(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok("not json {{"))
        assert get_helm_history("myapp") == []

    def test_returns_empty_list_on_empty_history(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok("[]"))
        assert get_helm_history("myapp") == []

    def test_passes_namespace_to_command(self, mocker):
        mock_run = mocker.patch("backend.pipeline.deployer.run_command",
                                return_value=_ok("[]"))
        get_helm_history("myapp", namespace="staging")
        cmd = mock_run.call_args[0][0]
        assert "staging" in cmd


# ---------------------------------------------------------------------------
# deploy_helm
# ---------------------------------------------------------------------------

class TestDeployHelm:
    def _patch_helm_found(self, mocker):
        mocker.patch("backend.pipeline.deployer._helm_available", return_value=True)

    def _patch_chart_found(self, mocker, path="k8s/helm/guardops-app"):
        mocker.patch("backend.pipeline.deployer._find_chart_path", return_value=path)

    def _patch_revision(self, mocker, revision=1):
        mocker.patch("backend.pipeline.deployer._get_helm_revision", return_value=revision)

    def _patch_ingress(self, mocker, url="http://test-app.local"):
        mocker.patch("backend.pipeline.deployer._get_ingress_url", return_value=url)

    def test_returns_failure_when_helm_not_installed(self, mocker):
        mocker.patch("backend.pipeline.deployer._helm_available", return_value=False)
        result = deploy_helm("myapp", "myapp:v1")
        assert result.success is False
        assert "helm not found" in result.error_message

    def test_returns_failure_when_chart_not_found(self, mocker):
        self._patch_helm_found(mocker)
        mocker.patch("backend.pipeline.deployer._find_chart_path", return_value=None)
        result = deploy_helm("myapp", "myapp:v1")
        assert result.success is False
        assert "chart not found" in result.error_message.lower()

    def test_success_local_env(self, mocker):
        self._patch_helm_found(mocker)
        self._patch_chart_found(mocker)
        self._patch_revision(mocker, revision=1)
        self._patch_ingress(mocker, url="http://test-app.local")
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        result = deploy_helm("myapp", "myapp:abc123", env="local")
        assert result.success is True
        assert result.helm_release == "myapp"
        assert result.helm_revision == 1
        assert result.service_url == "http://test-app.local"

    def test_success_prod_env(self, mocker):
        self._patch_helm_found(mocker)
        self._patch_chart_found(mocker)
        self._patch_revision(mocker, revision=2)
        self._patch_ingress(mocker, url="http://app.yourdomain.com")
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        # Patch Path.exists for values-prod.yaml
        mocker.patch("pathlib.Path.exists", return_value=True)
        result = deploy_helm(
            "myapp",
            "123.dkr.ecr.us-east-1.amazonaws.com/myapp:abc123",
            env="prod"
        )
        assert result.success is True
        assert result.helm_revision == 2

    def test_helm_failure_returns_error_result(self, mocker):
        self._patch_helm_found(mocker)
        self._patch_chart_found(mocker)
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_fail())
        result = deploy_helm("myapp", "myapp:v1")
        assert result.success is False
        assert "helm upgrade" in result.error_message.lower() or \
               "rolled back" in result.error_message.lower()

    def test_image_split_correctly_in_command(self, mocker):
        self._patch_helm_found(mocker)
        self._patch_chart_found(mocker)
        self._patch_revision(mocker)
        self._patch_ingress(mocker)
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        deploy_helm("myapp", "myapp:sha123", env="local")
        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "image.repository=myapp" in cmd_str
        assert "image.tag=sha123" in cmd_str

    def test_pull_policy_never_for_local(self, mocker):
        self._patch_helm_found(mocker)
        self._patch_chart_found(mocker)
        self._patch_revision(mocker)
        self._patch_ingress(mocker)
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        deploy_helm("myapp", "myapp:v1", env="local")
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert "pullPolicy=Never" in cmd_str

    def test_pull_policy_always_for_prod(self, mocker):
        self._patch_helm_found(mocker)
        self._patch_chart_found(mocker)
        self._patch_revision(mocker)
        self._patch_ingress(mocker)
        mocker.patch("pathlib.Path.exists", return_value=False)
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        deploy_helm("myapp", "123.ecr.amazonaws.com/myapp:v1", env="prod")
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert "pullPolicy=Always" in cmd_str

    def test_atomic_flag_always_present(self, mocker):
        self._patch_helm_found(mocker)
        self._patch_chart_found(mocker)
        self._patch_revision(mocker)
        self._patch_ingress(mocker)
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        deploy_helm("myapp", "myapp:v1")
        assert "--atomic" in mock_run.call_args[0][0]

    def test_create_namespace_flag_present(self, mocker):
        self._patch_helm_found(mocker)
        self._patch_chart_found(mocker)
        self._patch_revision(mocker)
        self._patch_ingress(mocker)
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        deploy_helm("myapp", "myapp:v1", namespace="staging")
        assert "--create-namespace" in mock_run.call_args[0][0]

    def test_extra_values_added_as_set_flags(self, mocker):
        self._patch_helm_found(mocker)
        self._patch_chart_found(mocker)
        self._patch_revision(mocker)
        self._patch_ingress(mocker)
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        deploy_helm("myapp", "myapp:v1", extra_values={"ingress.host": "myapp.example.com"})
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert "ingress.host=myapp.example.com" in cmd_str

    def test_replicas_set_in_command(self, mocker):
        self._patch_helm_found(mocker)
        self._patch_chart_found(mocker)
        self._patch_revision(mocker)
        self._patch_ingress(mocker)
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        deploy_helm("myapp", "myapp:v1", replicas=3)
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert "replicaCount=3" in cmd_str

    def test_release_name_sanitized(self, mocker):
        self._patch_helm_found(mocker)
        self._patch_chart_found(mocker)
        self._patch_revision(mocker)
        self._patch_ingress(mocker)
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        result = deploy_helm("My App_v2", "myapp:v1")
        assert result.helm_release == "my-app-v2"


# ---------------------------------------------------------------------------
# rollback_helm
# ---------------------------------------------------------------------------

class TestRollbackHelm:
    def test_returns_failure_when_helm_not_installed(self, mocker):
        mocker.patch("backend.pipeline.deployer._helm_available", return_value=False)
        result = rollback_helm("myapp")
        assert result.success is False
        assert "helm not found" in result.error_message

    def test_success_rollback_to_previous(self, mocker):
        mocker.patch("backend.pipeline.deployer._helm_available", return_value=True)
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        mocker.patch("backend.pipeline.deployer._get_helm_revision", return_value=2)
        result = rollback_helm("myapp", revision=0)
        assert result.success is True
        assert result.rolled_back_to == 2
        assert result.release == "myapp"

    def test_success_rollback_to_specific_revision(self, mocker):
        mocker.patch("backend.pipeline.deployer._helm_available", return_value=True)
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        mocker.patch("backend.pipeline.deployer._get_helm_revision", return_value=3)
        result = rollback_helm("myapp", revision=3)
        assert result.success is True
        assert result.rolled_back_to == 3

    def test_failure_returns_error_message(self, mocker):
        mocker.patch("backend.pipeline.deployer._helm_available", return_value=True)
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_fail("release not found"))
        result = rollback_helm("myapp")
        assert result.success is False
        assert "release not found" in result.error_message

    def test_wait_flag_in_command(self, mocker):
        mocker.patch("backend.pipeline.deployer._helm_available", return_value=True)
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        mocker.patch("backend.pipeline.deployer._get_helm_revision", return_value=1)
        rollback_helm("myapp")
        assert "--wait" in mock_run.call_args[0][0]

    def test_namespace_passed_to_command(self, mocker):
        mocker.patch("backend.pipeline.deployer._helm_available", return_value=True)
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok())
        mocker.patch("backend.pipeline.deployer._get_helm_revision", return_value=1)
        rollback_helm("myapp", namespace="staging")
        cmd = mock_run.call_args[0][0]
        assert "staging" in cmd
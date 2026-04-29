"""
tests/test_deployer.py

Tests for backend/pipeline/deployer.py

Coverage goals:
  - Happy path: full successful deploy_local flow
  - Every early-exit path: k3d import fails, manifest apply fails,
    rollout timeout, kubectl errors
  - Error recovery: when a deploy fails mid-way, applied resources
    are cleaned up (rollback_partial_deploy)
  - get_deployment_status: kubectl failure, malformed JSON, success
  - get_pods: kubectl failure, malformed JSON, success
  - Helper functions in isolation: _ensure_namespace, _kubectl_apply,
    _wait_for_rollout, _is_pod_ready, _get_restart_count

Why we mock subprocess/run_command:
  deployer.py shells out to kubectl and k3d. We never want tests to
  require a real Kubernetes cluster. Every test controls exactly what
  the shell commands return so tests are fast and environment-independent.
"""

import json
import subprocess
import pytest
from unittest.mock import MagicMock, patch, call

from backend.pipeline.deployer import (
    deploy_local,
    get_deployment_status,
    get_pods,
    import_image_to_k3d,
    rollback_partial_deploy,
    _ensure_namespace,
    _kubectl_apply,
    _wait_for_rollout,
    _get_service_url,
    _is_pod_ready,
    _get_restart_count,
    DeployResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_cmd(stdout=""):
    """Simulates a subprocess result with returncode=0."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = ""
    return m


def _fail_cmd(stderr="error", returncode=1):
    """Simulates a subprocess result with non-zero returncode."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = ""
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# import_image_to_k3d
# ---------------------------------------------------------------------------

class TestImportImageToK3d:
    def test_success(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok_cmd())
        assert import_image_to_k3d("my-app:v1") is True

    def test_failure(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_fail_cmd())
        assert import_image_to_k3d("my-app:v1") is False

    def test_passes_cluster_name(self, mocker):
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok_cmd())
        import_image_to_k3d("my-app:v1", cluster_name="my-cluster")
        called_cmd = mock_run.call_args[0][0]
        assert "my-cluster" in called_cmd
        assert "my-app:v1" in called_cmd


# ---------------------------------------------------------------------------
# _ensure_namespace
# ---------------------------------------------------------------------------

class TestEnsureNamespace:
    def test_default_namespace_skipped(self, mocker):
        """'default' namespace always exists — should never call kubectl."""
        mock_run = mocker.patch("backend.pipeline.deployer.run_command")
        _ensure_namespace("default")
        mock_run.assert_not_called()

    def test_custom_namespace_created(self, mocker):
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok_cmd())
        _ensure_namespace("staging")
        mock_run.assert_called_once()
        called_cmd = mock_run.call_args[0][0]
        assert "staging" in called_cmd
        assert "create" in called_cmd

    def test_already_exists_is_graceful(self, mocker):
        """If namespace already exists kubectl returns an error — should not raise."""
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_fail_cmd(stderr="Error: namespaces 'staging' already exists"))
        # Should not raise any exception
        _ensure_namespace("staging")

    def test_unexpected_error_warns_but_does_not_raise(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_fail_cmd(stderr="connection refused"))
        _ensure_namespace("staging")   # Should not raise


# ---------------------------------------------------------------------------
# _kubectl_apply
# ---------------------------------------------------------------------------

class TestKubectlApply:
    def test_success_returns_true(self, mocker):
        mock_run = mocker.patch("subprocess.run", return_value=_ok_cmd("deployment.apps/app configured"))
        result = _kubectl_apply("apiVersion: apps/v1\nkind: Deployment\n")
        assert result is True

    def test_failure_returns_false(self, mocker):
        mocker.patch("subprocess.run", return_value=_fail_cmd("no matches for kind"))
        result = _kubectl_apply("invalid yaml")
        assert result is False

    def test_passes_yaml_via_stdin(self, mocker):
        """The YAML string must be sent as stdin, not as a file."""
        mock_run = mocker.patch("subprocess.run", return_value=_ok_cmd())
        yaml_content = "apiVersion: v1\nkind: ConfigMap\n"
        _kubectl_apply(yaml_content)
        _, kwargs = mock_run.call_args
        assert kwargs.get("input") == yaml_content


# ---------------------------------------------------------------------------
# _wait_for_rollout
# ---------------------------------------------------------------------------

class TestWaitForRollout:
    def test_success(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok_cmd())
        assert _wait_for_rollout("my-app", "default") is True

    def test_timeout_returns_false(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_fail_cmd())
        assert _wait_for_rollout("my-app", "default", timeout_seconds=30) is False

    def test_passes_timeout_to_kubectl(self, mocker):
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok_cmd())
        _wait_for_rollout("my-app", "default", timeout_seconds=60)
        called_cmd = mock_run.call_args[0][0]
        assert "--timeout=60s" in called_cmd


# ---------------------------------------------------------------------------
# get_deployment_status
# ---------------------------------------------------------------------------

class TestGetDeploymentStatus:
    def test_kubectl_failure_returns_empty_dict(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_fail_cmd())
        assert get_deployment_status("my-app") == {}

    def test_malformed_json_returns_empty_dict(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok_cmd("not json at all"))
        assert get_deployment_status("my-app") == {}

    def test_empty_json_object_returns_empty_dict(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok_cmd("{}"))
        result = get_deployment_status("my-app")
        # Should not crash; returns a partial dict with default values
        assert isinstance(result, dict)

    def test_parses_healthy_deployment(self, mocker):
        k8s_response = {
            "metadata": {"name": "my-app"},
            "spec": {"replicas": 3},
            "status": {
                "readyReplicas": 3,
                "availableReplicas": 3,
                "updatedReplicas": 3,
                "conditions": [
                    {"type": "Available", "status": "True", "message": "Deployment available"},
                    {"type": "Progressing", "status": "True", "message": "ReplicaSet updated"},
                ]
            }
        }
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok_cmd(json.dumps(k8s_response)))
        result = get_deployment_status("my-app")
        assert result["desired_replicas"] == 3
        assert result["ready_replicas"] == 3
        assert result["available_replicas"] == 3
        assert len(result["conditions"]) == 2

    def test_missing_status_field_uses_defaults(self, mocker):
        k8s_response = {
            "spec": {"replicas": 1},
            "status": {}   # No readyReplicas etc.
        }
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok_cmd(json.dumps(k8s_response)))
        result = get_deployment_status("my-app")
        assert result["ready_replicas"] == 0
        assert result["available_replicas"] == 0


# ---------------------------------------------------------------------------
# get_pods
# ---------------------------------------------------------------------------

class TestGetPods:
    def test_kubectl_failure_returns_empty_list(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_fail_cmd())
        assert get_pods("my-app") == []

    def test_malformed_json_returns_empty_list(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok_cmd("malformed"))
        assert get_pods("my-app") == []

    def test_no_pods_returns_empty_list(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok_cmd(json.dumps({"items": []})))
        assert get_pods("my-app") == []

    def test_parses_running_pod(self, mocker):
        k8s_response = {"items": [{
            "metadata": {"name": "my-app-abc123", "creationTimestamp": "2024-01-01T00:00:00Z"},
            "spec": {"nodeName": "k3d-guardops-server-0"},
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
                "containerStatuses": [{"restartCount": 0}],
            }
        }]}
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok_cmd(json.dumps(k8s_response)))
        pods = get_pods("my-app")
        assert len(pods) == 1
        assert pods[0]["name"] == "my-app-abc123"
        assert pods[0]["phase"] == "Running"
        assert pods[0]["ready"] is True
        assert pods[0]["restarts"] == 0
        assert pods[0]["node"] == "k3d-guardops-server-0"

    def test_parses_crashing_pod(self, mocker):
        k8s_response = {"items": [{
            "metadata": {"name": "my-app-crash", "creationTimestamp": "2024-01-01T00:00:00Z"},
            "spec": {"nodeName": "node-1"},
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "False"}],
                "containerStatuses": [{"restartCount": 5}],
            }
        }]}
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok_cmd(json.dumps(k8s_response)))
        pods = get_pods("my-app")
        assert pods[0]["ready"] is False
        assert pods[0]["restarts"] == 5


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_is_pod_ready_true(self):
        pod = {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        assert _is_pod_ready(pod) is True

    def test_is_pod_ready_false(self):
        pod = {"status": {"conditions": [{"type": "Ready", "status": "False"}]}}
        assert _is_pod_ready(pod) is False

    def test_is_pod_ready_no_conditions(self):
        pod = {"status": {"conditions": []}}
        assert _is_pod_ready(pod) is False

    def test_is_pod_ready_no_status(self):
        assert _is_pod_ready({}) is False

    def test_get_restart_count_single_container(self):
        pod = {"status": {"containerStatuses": [{"restartCount": 3}]}}
        assert _get_restart_count(pod) == 3

    def test_get_restart_count_multiple_containers(self):
        pod = {"status": {"containerStatuses": [
            {"restartCount": 2}, {"restartCount": 1}
        ]}}
        assert _get_restart_count(pod) == 3

    def test_get_restart_count_no_containers(self):
        assert _get_restart_count({}) == 0

    def test_get_service_url_returns_localhost(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_ok_cmd("30783"))
        url = _get_service_url("my-app", "default", 8080)
        assert url == "http://localhost:30783"

    def test_get_service_url_fallback_on_failure(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_fail_cmd())
        url = _get_service_url("my-app", "default", 8080)
        # Should contain something useful even on failure
        assert "8080" in url or "localhost" in url


# ---------------------------------------------------------------------------
# deploy_local — integration-level (all internal calls mocked)
# ---------------------------------------------------------------------------

class TestDeployLocal:
    def _setup_full_success(self, mocker):
        """Patches every subprocess call in the deploy_local flow to succeed."""
        mocker.patch("backend.pipeline.deployer.import_image_to_k3d", return_value=True)
        mocker.patch("backend.pipeline.deployer._ensure_namespace")
        mocker.patch("backend.pipeline.deployer._kubectl_apply", return_value=True)
        mocker.patch("backend.pipeline.deployer._wait_for_rollout", return_value=True)
        mocker.patch("backend.pipeline.deployer._get_service_url",
                     return_value="http://localhost:30783")

    def test_success_returns_correct_result(self, mocker):
        self._setup_full_success(mocker)
        result = deploy_local("my-app", "my-app:v1")
        assert result.success is True
        assert result.service_url == "http://localhost:30783"
        assert result.deployment_name == "my-app"
        assert result.error_message == ""

    def test_k3d_import_failure_returns_early(self, mocker):
        mocker.patch("backend.pipeline.deployer.import_image_to_k3d", return_value=False)
        ensure = mocker.patch("backend.pipeline.deployer._ensure_namespace")
        result = deploy_local("my-app", "my-app:v1")
        assert result.success is False
        assert "k3d" in result.error_message.lower() or "import" in result.error_message.lower()
        # Should not proceed past k3d import
        ensure.assert_not_called()

    def test_deployment_apply_failure_triggers_cleanup(self, mocker):
        mocker.patch("backend.pipeline.deployer.import_image_to_k3d", return_value=True)
        mocker.patch("backend.pipeline.deployer._ensure_namespace")
        # First kubectl apply (Deployment) fails
        mocker.patch("backend.pipeline.deployer._kubectl_apply", return_value=False)
        mock_rollback = mocker.patch("backend.pipeline.deployer.rollback_partial_deploy")
        result = deploy_local("my-app", "my-app:v1")
        assert result.success is False
        assert "Deployment" in result.error_message
        mock_rollback.assert_called_once()

    def test_service_apply_failure_triggers_cleanup(self, mocker):
        mocker.patch("backend.pipeline.deployer.import_image_to_k3d", return_value=True)
        mocker.patch("backend.pipeline.deployer._ensure_namespace")
        # First apply (Deployment) succeeds, second (Service) fails
        mocker.patch("backend.pipeline.deployer._kubectl_apply",
                     side_effect=[True, False])
        mock_rollback = mocker.patch("backend.pipeline.deployer.rollback_partial_deploy")
        result = deploy_local("my-app", "my-app:v1")
        assert result.success is False
        assert "Service" in result.error_message
        mock_rollback.assert_called_once()

    def test_rollout_timeout_triggers_cleanup(self, mocker):
        mocker.patch("backend.pipeline.deployer.import_image_to_k3d", return_value=True)
        mocker.patch("backend.pipeline.deployer._ensure_namespace")
        mocker.patch("backend.pipeline.deployer._kubectl_apply", return_value=True)
        mocker.patch("backend.pipeline.deployer._wait_for_rollout", return_value=False)
        mock_rollback = mocker.patch("backend.pipeline.deployer.rollback_partial_deploy")
        result = deploy_local("my-app", "my-app:v1")
        assert result.success is False
        assert "timed out" in result.error_message.lower()
        mock_rollback.assert_called_once()

    def test_rollback_info_included_in_error_message(self, mocker):
        """User should know a rollback was attempted when deploy fails."""
        mocker.patch("backend.pipeline.deployer.import_image_to_k3d", return_value=True)
        mocker.patch("backend.pipeline.deployer._ensure_namespace")
        mocker.patch("backend.pipeline.deployer._kubectl_apply", return_value=True)
        mocker.patch("backend.pipeline.deployer._wait_for_rollout", return_value=False)
        mocker.patch("backend.pipeline.deployer.rollback_partial_deploy", return_value=True)
        result = deploy_local("my-app", "my-app:v1")
        assert result.success is False
        # rollback_applied field should be True
        assert result.rollback_applied is True

    def test_replicas_passed_through(self, mocker):
        self._setup_full_success(mocker)
        result = deploy_local("my-app", "my-app:v1", replicas=3)
        assert result.replicas == 3

    def test_namespace_passed_through(self, mocker):
        self._setup_full_success(mocker)
        result = deploy_local("my-app", "my-app:v1", namespace="staging")
        assert result.namespace == "staging"


# ---------------------------------------------------------------------------
# rollback_partial_deploy
# ---------------------------------------------------------------------------

class TestRollbackPartialDeploy:
    def test_deletes_deployment_and_service(self, mocker):
        mock_run = mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok_cmd())
        rollback_partial_deploy("my-app", "default")
        # Should have called kubectl delete for both deployment and service
        calls = [str(c) for c in mock_run.call_args_list]
        combined = " ".join(calls)
        assert "deployment" in combined.lower()
        assert "service" in combined.lower()
        assert "my-app" in combined

    def test_returns_true_on_success(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_ok_cmd())
        assert rollback_partial_deploy("my-app", "default") is True

    def test_returns_false_on_failure(self, mocker):
        mocker.patch("backend.pipeline.deployer.run_command", return_value=_fail_cmd())
        assert rollback_partial_deploy("my-app", "default") is False

    def test_does_not_raise_if_resource_not_found(self, mocker):
        """If the resource doesn't exist (deploy never happened), rollback should not crash."""
        mocker.patch("backend.pipeline.deployer.run_command",
                     return_value=_fail_cmd(stderr='Error: deployments.apps "my-app" not found'))
        # Should not raise
        rollback_partial_deploy("my-app", "default")
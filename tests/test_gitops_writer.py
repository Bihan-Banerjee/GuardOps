"""
tests/test_gitops_writer.py

Unit tests for backend/pipeline/gitops_writer.py — Phase 10.

Coverage:
  - write_image_override(): file content, path, edge cases
  - _split_image_ref():     ECR URLs, plain refs, no-tag refs
  - commit_and_push():      success, idempotent no-op, git failure
  - trigger_argocd_sync():  API success, HTTP error, network error
  - get_app_status():       healthy response, degraded, parse error, HTTP error
  - poll_until_healthy():   reaches healthy, degrades early, times out

All subprocess and requests calls are mocked so tests run without a
real git repo or a live ArgoCD server.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from backend.pipeline.gitops_writer import (
    GitOpsResult,
    SyncResult,
    _find_chart_dir,
    _get_current_sha,
    _split_image_ref,
    commit_and_push,
    get_app_status,
    poll_until_healthy,
    trigger_argocd_sync,
    write_image_override,
)


# ── _split_image_ref ──────────────────────────────────────────────────────────

class TestSplitImageRef:
    """Tests for the private ECR image-ref splitter."""

    def test_ecr_url_with_tag(self):
        repo, tag = _split_image_ref(
            "123456789.dkr.ecr.ap-south-1.amazonaws.com/guardops-app:abc1234"
        )
        assert repo == "123456789.dkr.ecr.ap-south-1.amazonaws.com/guardops-app"
        assert tag  == "abc1234"

    def test_simple_name_with_tag(self):
        repo, tag = _split_image_ref("guardops-app:latest")
        assert repo == "guardops-app"
        assert tag  == "latest"

    def test_no_tag_returns_latest(self):
        repo, tag = _split_image_ref("guardops-app")
        assert repo == "guardops-app"
        assert tag  == "latest"

    def test_staging_prefixed_tag(self):
        repo, tag = _split_image_ref(
            "123456789.dkr.ecr.ap-south-1.amazonaws.com/guardops-app:staging-abc1234"
        )
        assert tag == "staging-abc1234"

    def test_image_with_port_in_registry(self):
        """Registry hostname may include a port — rfind(':') must pick the last one."""
        repo, tag = _split_image_ref("registry.local:5000/myapp:v1.2.3")
        assert repo == "registry.local:5000/myapp"
        assert tag  == "v1.2.3"


# ── write_image_override ──────────────────────────────────────────────────────

class TestWriteImageOverride:
    """Tests for the values-override-<env>.yaml writer."""

    def test_writes_correct_content_prod(self, tmp_path):
        override_path = write_image_override(
            env="prod",
            image_ref="123456789.dkr.ecr.ap-south-1.amazonaws.com/guardops-app:abc1234",
            chart_dir=tmp_path,
        )
        content = override_path.read_text()

        assert "repository: 123456789.dkr.ecr.ap-south-1.amazonaws.com/guardops-app" in content
        assert 'tag: "abc1234"' in content
        assert "pullPolicy: Always" in content
        assert "Auto-generated" in content
        assert "Env: prod" in content

    def test_writes_correct_content_staging(self, tmp_path):
        override_path = write_image_override(
            env="staging",
            image_ref="123456789.dkr.ecr.ap-south-1.amazonaws.com/guardops-app:staging-abc1234",
            chart_dir=tmp_path,
        )
        content = override_path.read_text()

        assert 'tag: "staging-abc1234"' in content
        assert "Env: staging" in content

    def test_returns_correct_path(self, tmp_path):
        path = write_image_override(
            env="prod",
            image_ref="myrepo/app:v1",
            chart_dir=tmp_path,
        )
        assert path == tmp_path / "values-override-prod.yaml"
        assert path.exists()

    def test_overwrites_existing_file(self, tmp_path):
        """Calling write_image_override twice replaces the old file."""
        write_image_override(env="prod", image_ref="repo/app:v1", chart_dir=tmp_path)
        write_image_override(env="prod", image_ref="repo/app:v2", chart_dir=tmp_path)

        content = (tmp_path / "values-override-prod.yaml").read_text()
        assert 'tag: "v2"' in content
        assert "v1" not in content

    def test_auto_detect_chart_dir(self, monkeypatch, tmp_path):
        """_find_chart_dir returns tmp_path when it exists at the expected location."""
        chart_path = tmp_path / "k8s" / "helm" / "guardops-app"
        chart_path.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)

        path = write_image_override(
            env="prod",
            image_ref="repo/app:abc",
        )
        assert path.name == "values-override-prod.yaml"


# ── commit_and_push ───────────────────────────────────────────────────────────

class TestCommitAndPush:
    """Tests for the git commit + push step."""

    @patch("backend.pipeline.gitops_writer.subprocess.run")
    @patch("backend.pipeline.gitops_writer._run_git")
    @patch("backend.pipeline.gitops_writer._get_current_sha", return_value="abc1234")
    def test_successful_commit_and_push(
        self, mock_sha, mock_run_git, mock_subprocess, tmp_path
    ):
        """Happy path: file has changes → commit is made → push succeeds."""
        override_path = tmp_path / "values-override-prod.yaml"
        override_path.write_text("image:\n  tag: abc1234\n")

        # `git diff --cached --quiet` returning 1 means there IS a diff (changes staged)
        mock_subprocess.return_value = MagicMock(returncode=1)

        result = commit_and_push(
            override_path=override_path,
            env="prod",
            image_tag="abc1234",
            branch="main",
        )

        assert result.success is True
        assert result.commit_sha == "abc1234"
        assert result.branch == "main"
        assert result.skipped is False
        assert str(override_path) == result.override_file

        # Verify git operations were called
        git_calls = [str(c) for c in mock_run_git.call_args_list]
        assert any("commit" in c for c in git_calls)
        assert any("push" in c for c in git_calls)

    @patch("backend.pipeline.gitops_writer.subprocess.run")
    @patch("backend.pipeline.gitops_writer._run_git")
    @patch("backend.pipeline.gitops_writer._get_current_sha", return_value="abc1234")
    def test_idempotent_no_op_when_no_diff(
        self, mock_sha, mock_run_git, mock_subprocess, tmp_path
    ):
        """If the override file has no staged changes, returns skipped=True."""
        override_path = tmp_path / "values-override-prod.yaml"
        override_path.write_text("image:\n  tag: abc1234\n")

        # returncode=0 means no diff → nothing to commit
        mock_subprocess.return_value = MagicMock(returncode=0)

        result = commit_and_push(
            override_path=override_path,
            env="prod",
            image_tag="abc1234",
            branch="main",
        )

        assert result.success is True
        assert result.skipped is True
        assert "already committed" in result.skip_reason.lower() or "unchanged" in result.skip_reason.lower()

        # No commit or push should have been attempted
        commit_calls = [c for c in mock_run_git.call_args_list if "commit" in str(c)]
        push_calls   = [c for c in mock_run_git.call_args_list if "push" in str(c)]
        assert len(commit_calls) == 0
        assert len(push_calls)   == 0

    @patch("backend.pipeline.gitops_writer.subprocess.run")
    @patch("backend.pipeline.gitops_writer._run_git")
    def test_git_failure_returns_error_result(
        self, mock_run_git, mock_subprocess, tmp_path
    ):
        """subprocess.CalledProcessError from git is captured as GitOpsResult.success=False."""
        override_path = tmp_path / "values-override-prod.yaml"
        override_path.write_text("image:\n  tag: abc1234\n")

        mock_run_git.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["git", "push"],
            stderr="remote: Permission to repo denied",
        )

        result = commit_and_push(
            override_path=override_path,
            env="prod",
            image_tag="abc1234",
        )

        assert result.success is False
        assert "Permission to repo denied" in result.error_message

    @patch("backend.pipeline.gitops_writer.subprocess.run")
    @patch("backend.pipeline.gitops_writer._run_git")
    @patch("backend.pipeline.gitops_writer._get_current_sha", return_value="def5678")
    def test_commit_message_format(
        self, mock_sha, mock_run_git, mock_subprocess, tmp_path
    ):
        """Commit message follows Conventional Commits with [skip ci]."""
        override_path = tmp_path / "values-override-staging.yaml"
        override_path.write_text("image:\n  tag: staging-def5678\n")
        mock_subprocess.return_value = MagicMock(returncode=1)

        commit_and_push(
            override_path=override_path,
            env="staging",
            image_tag="staging-def5678",
            branch="main",
        )

        commit_calls = [
            c for c in mock_run_git.call_args_list
            if c[0][0][0] == "commit"
        ]
        assert len(commit_calls) == 1
        commit_args = commit_calls[0][0][0]
        commit_msg  = commit_args[commit_args.index("-m") + 1]

        assert "chore(gitops)" in commit_msg
        assert "staging" in commit_msg
        assert "staging-def5678" in commit_msg
        assert "[skip ci]" in commit_msg

    @patch("backend.pipeline.gitops_writer.subprocess.run")
    @patch("backend.pipeline.gitops_writer._run_git")
    @patch("backend.pipeline.gitops_writer._get_current_sha", return_value="abc1234")
    def test_bot_identity_configured(
        self, mock_sha, mock_run_git, mock_subprocess, tmp_path
    ):
        """Git user.email and user.name are set to the bot identity."""
        override_path = tmp_path / "values-override-prod.yaml"
        override_path.write_text("image:\n  tag: abc\n")
        mock_subprocess.return_value = MagicMock(returncode=1)

        commit_and_push(override_path=override_path, env="prod", image_tag="abc")

        all_git_args = [str(c) for c in mock_run_git.call_args_list]
        assert any("guardops-bot@users.noreply.github.com" in a for a in all_git_args)
        assert any("guardops-bot" in a for a in all_git_args)


# ── trigger_argocd_sync ───────────────────────────────────────────────────────

class TestTriggerArgocdSync:
    """Tests for the ArgoCD REST API sync trigger."""

    def test_successful_sync_trigger(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("backend.pipeline.gitops_writer.requests.post", return_value=mock_resp):
            result = trigger_argocd_sync(
                app_name="guardops-app-prod",
                argocd_url="https://argocd.guardops.dev",
                token="test-token",
            )

        assert result.success is True
        assert result.app_name == "guardops-app-prod"

    def test_accepted_202_is_success(self):
        """HTTP 202 Accepted is also a valid success response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 202

        with patch("backend.pipeline.gitops_writer.requests.post", return_value=mock_resp):
            result = trigger_argocd_sync(
                app_name="guardops-app-prod",
                argocd_url="https://argocd.guardops.dev",
                token="token",
            )
        assert result.success is True

    def test_http_error_returns_failure(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = '{"error": "permission denied"}'

        with patch("backend.pipeline.gitops_writer.requests.post", return_value=mock_resp):
            result = trigger_argocd_sync(
                app_name="guardops-app-prod",
                argocd_url="https://argocd.guardops.dev",
                token="bad-token",
            )

        assert result.success is False
        assert "403" in result.error_message

    def test_network_error_returns_failure(self):
        with patch(
            "backend.pipeline.gitops_writer.requests.post",
            side_effect=requests.ConnectionError("Connection refused"),
        ):
            result = trigger_argocd_sync(
                app_name="guardops-app-prod",
                argocd_url="https://argocd.guardops.dev",
                token="token",
            )

        assert result.success is False
        assert "Connection refused" in result.error_message

    def test_correct_api_url_constructed(self):
        mock_resp = MagicMock(status_code=200)
        with patch("backend.pipeline.gitops_writer.requests.post", return_value=mock_resp) as mock_post:
            trigger_argocd_sync(
                app_name="guardops-app-prod",
                argocd_url="https://argocd.guardops.dev",
                token="tok",
            )
        called_url = mock_post.call_args[0][0]
        assert called_url == "https://argocd.guardops.dev/api/v1/applications/guardops-app-prod/sync"

    def test_trailing_slash_in_url_handled(self):
        """Trailing slash on argocd_url must not produce double-slash in API path."""
        mock_resp = MagicMock(status_code=200)
        with patch("backend.pipeline.gitops_writer.requests.post", return_value=mock_resp) as mock_post:
            trigger_argocd_sync(
                app_name="guardops-app-prod",
                argocd_url="https://argocd.guardops.dev/",
                token="tok",
            )
        called_url = mock_post.call_args[0][0]
        assert "//api" not in called_url


# ── get_app_status ────────────────────────────────────────────────────────────

class TestGetAppStatus:
    """Tests for the single-shot Application status poller."""

    def _make_argocd_response(
        self,
        sync_status: str = "Synced",
        health_status: str = "Healthy",
        revision: str = "abcdef1234567890",
    ) -> dict:
        return {
            "status": {
                "sync":   {"status": sync_status,   "revision": revision},
                "health": {"status": health_status},
            }
        }

    def test_healthy_synced_response(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self._make_argocd_response("Synced", "Healthy")

        with patch("backend.pipeline.gitops_writer.requests.get", return_value=mock_resp):
            result = get_app_status("guardops-app-prod", "https://argocd.dev", "tok")

        assert result.success       is True
        assert result.sync_status   == "Synced"
        assert result.health_status == "Healthy"
        assert result.revision      == "abcdef1"   # short SHA (7 chars)

    def test_degraded_response(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self._make_argocd_response("Synced", "Degraded")

        with patch("backend.pipeline.gitops_writer.requests.get", return_value=mock_resp):
            result = get_app_status("guardops-app-prod", "https://argocd.dev", "tok")

        assert result.success       is True
        assert result.health_status == "Degraded"

    def test_out_of_sync_progressing(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self._make_argocd_response("OutOfSync", "Progressing")

        with patch("backend.pipeline.gitops_writer.requests.get", return_value=mock_resp):
            result = get_app_status("guardops-app-prod", "https://argocd.dev", "tok")

        assert result.sync_status   == "OutOfSync"
        assert result.health_status == "Progressing"

    def test_http_401_returns_failure(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        with patch("backend.pipeline.gitops_writer.requests.get", return_value=mock_resp):
            result = get_app_status("guardops-app-prod", "https://argocd.dev", "bad-token")

        assert result.success is False
        assert "401" in result.error_message

    def test_network_error_returns_failure(self):
        with patch(
            "backend.pipeline.gitops_writer.requests.get",
            side_effect=requests.Timeout("Read timed out"),
        ):
            result = get_app_status("guardops-app-prod", "https://argocd.dev", "tok")

        assert result.success is False
        assert "timed out" in result.error_message.lower()

    def test_malformed_json_returns_failure(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("invalid JSON")

        with patch("backend.pipeline.gitops_writer.requests.get", return_value=mock_resp):
            result = get_app_status("guardops-app-prod", "https://argocd.dev", "tok")

        assert result.success is False
        assert "parse" in result.error_message.lower()

    def test_revision_truncated_to_7_chars(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self._make_argocd_response(
            revision="abcdef1234567890abcdef1234567890abcdef12"  # full 40-char SHA
        )
        with patch("backend.pipeline.gitops_writer.requests.get", return_value=mock_resp):
            result = get_app_status("guardops-app-prod", "https://argocd.dev", "tok")

        assert len(result.revision) == 7
        assert result.revision == "abcdef1"

    def test_missing_status_fields_default_to_unknown(self):
        """If ArgoCD returns an Application with no status block, defaults to Unknown."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}   # empty response

        with patch("backend.pipeline.gitops_writer.requests.get", return_value=mock_resp):
            result = get_app_status("guardops-app-prod", "https://argocd.dev", "tok")

        assert result.sync_status   == "Unknown"
        assert result.health_status == "Unknown"


# ── poll_until_healthy ────────────────────────────────────────────────────────

class TestPollUntilHealthy:
    """Tests for the blocking ArgoCD health poller."""

    def _make_status(
        self,
        sync: str = "Synced",
        health: str = "Healthy",
    ) -> SyncResult:
        return SyncResult(
            success=True,
            app_name="guardops-app-prod",
            sync_status=sync,
            health_status=health,
            revision="abc1234",
        )

    @patch("backend.pipeline.gitops_writer.time.sleep")
    @patch("backend.pipeline.gitops_writer.get_app_status")
    def test_returns_healthy_immediately(self, mock_status, mock_sleep):
        """If the app is already Healthy on first poll, returns immediately."""
        mock_status.return_value = self._make_status("Synced", "Healthy")

        result = poll_until_healthy(
            app_name="guardops-app-prod",
            argocd_url="https://argocd.dev",
            token="tok",
            timeout_seconds=60,
        )

        assert result.success       is True
        assert result.sync_status   == "Synced"
        assert result.health_status == "Healthy"
        assert mock_status.call_count == 1

    @patch("backend.pipeline.gitops_writer.time.sleep")
    @patch("backend.pipeline.gitops_writer.get_app_status")
    def test_polls_until_healthy_after_progressing(self, mock_status, mock_sleep):
        """Progressing → Progressing → Healthy: returns success after 3 polls."""
        mock_status.side_effect = [
            self._make_status("OutOfSync",  "Progressing"),
            self._make_status("Synced",     "Progressing"),
            self._make_status("Synced",     "Healthy"),
        ]

        result = poll_until_healthy(
            app_name="guardops-app-prod",
            argocd_url="https://argocd.dev",
            token="tok",
            timeout_seconds=300,
        )

        assert result.success is True
        assert mock_status.call_count == 3

    @patch("backend.pipeline.gitops_writer.time.sleep")
    @patch("backend.pipeline.gitops_writer.get_app_status")
    def test_fails_fast_on_degraded(self, mock_status, mock_sleep):
        """Degraded health stops the poller immediately and returns success=False."""
        mock_status.side_effect = [
            self._make_status("OutOfSync",  "Progressing"),
            self._make_status("Synced",     "Degraded"),
        ]

        result = poll_until_healthy(
            app_name="guardops-app-prod",
            argocd_url="https://argocd.dev",
            token="tok",
            timeout_seconds=300,
        )

        assert result.success is False
        assert "Degraded" in result.error_message
        assert mock_status.call_count == 2   # stopped after first Degraded

    @patch("backend.pipeline.gitops_writer.time.monotonic")
    @patch("backend.pipeline.gitops_writer.time.sleep")
    @patch("backend.pipeline.gitops_writer.get_app_status")
    def test_timeout_returns_failure(self, mock_status, mock_sleep, mock_time):
        """When timeout is exceeded, returns success=False with timeout message."""
        # Simulate time advancing: 0s, 10s, 20s, ... until past timeout
        mock_time.side_effect = [0, 10, 20, 30, 40, 50, 60]
        mock_status.return_value = self._make_status("OutOfSync", "Progressing")

        result = poll_until_healthy(
            app_name="guardops-app-prod",
            argocd_url="https://argocd.dev",
            token="tok",
            timeout_seconds=30,
        )

        assert result.success is False
        assert "Timeout" in result.error_message or "timeout" in result.error_message.lower()

    @patch("backend.pipeline.gitops_writer.time.sleep")
    @patch("backend.pipeline.gitops_writer.get_app_status")
    def test_api_error_retries_without_failing(self, mock_status, mock_sleep):
        """A transient API error causes a retry, not an immediate failure."""
        mock_status.side_effect = [
            SyncResult(success=False, app_name="a", error_message="timeout"),
            SyncResult(success=False, app_name="a", error_message="timeout"),
            self._make_status("Synced", "Healthy"),
        ]

        result = poll_until_healthy(
            app_name="guardops-app-prod",
            argocd_url="https://argocd.dev",
            token="tok",
            timeout_seconds=300,
        )

        assert result.success is True
        assert mock_status.call_count == 3

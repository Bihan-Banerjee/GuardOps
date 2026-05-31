"""
tests/test_sync_cmd.py

Unit tests for cli/commands/sync_cmd.py — Phase 10.

Tests the `guardops sync-status` Click command using the Click test runner
(CliRunner) so no actual ArgoCD server or kubectl is needed.

Coverage:
  - Missing ArgoCD URL → clear error and exit 1
  - Missing ARGOCD_TOKEN env var → clear error and exit 1
  - Snapshot mode: Synced + Healthy → exit 0, success message
  - Snapshot mode: Degraded → exit 1, error message
  - Snapshot mode: OutOfSync → exit 0 (transient), warning shown
  - Wait mode: poll reaches Healthy → exit 0
  - Wait mode: poll times out → exit 1
  - Config resolution: app_name read from argocd.app_name_prod
  - ArgoCD UI link printed in output
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.commands.sync_cmd import sync_status_command
from backend.pipeline.gitops_writer import SyncResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

MINIMAL_CONFIG = {
    "project": {"name": "guardops-app"},
    "environments": {
        "prod":    {"domain": "guardops.live"},
        "staging": {"domain": "staging.guardops.live"},
    },
    "argocd": {
        "url":              "https://argocd.guardops.live",
        "app_name_prod":    "guardops-app-prod",
        "app_name_staging": "guardops-app-staging",
        "token_env_var":    "ARGOCD_TOKEN",
    },
}

HEALTHY_RESULT = SyncResult(
    success=True,
    app_name="guardops-app-prod",
    sync_status="Synced",
    health_status="Healthy",
    revision="abc1234",
)

DEGRADED_RESULT = SyncResult(
    success=True,
    app_name="guardops-app-prod",
    sync_status="Synced",
    health_status="Degraded",
    revision="abc1234",
)

OUT_OF_SYNC_RESULT = SyncResult(
    success=True,
    app_name="guardops-app-prod",
    sync_status="OutOfSync",
    health_status="Progressing",
    revision="abc1234",
)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_load_config():
    with patch("cli.commands.sync_cmd.load_config", return_value=MINIMAL_CONFIG):
        yield


# ── Missing config / env var errors ───────────────────────────────────────────

class TestConfigValidation:

    def test_missing_argocd_url_exits_1(self, runner):
        config = {**MINIMAL_CONFIG, "argocd": {"url": "", "app_name_prod": "guardops-app-prod", "token_env_var": "ARGOCD_TOKEN"}}
        with patch("cli.commands.sync_cmd.load_config", return_value=config):
            result = runner.invoke(sync_status_command, ["--env", "prod"])

        assert result.exit_code == 1
        assert "ArgoCD URL not configured" in result.output

    def test_missing_token_env_var_exits_1(self, runner, mock_load_config):
        env = {"ARGOCD_TOKEN": ""}   # explicitly empty
        result = runner.invoke(sync_status_command, ["--env", "prod"], env=env)
        assert result.exit_code == 1
        assert "ARGOCD_TOKEN" in result.output

    def test_argocd_url_from_cli_flag_overrides_config(self, runner):
        """--argocd-url flag overrides argocd.url in config."""
        config = {**MINIMAL_CONFIG, "argocd": {"url": "", "app_name_prod": "guardops-app-prod", "token_env_var": "ARGOCD_TOKEN"}}
        healthy = HEALTHY_RESULT

        with patch("cli.commands.sync_cmd.load_config", return_value=config):
            with patch("cli.commands.sync_cmd.get_app_status", return_value=healthy):
                result = runner.invoke(
                    sync_status_command,
                    ["--env", "prod", "--argocd-url", "https://argocd.example.com"],
                    env={"ARGOCD_TOKEN": "tok"},
                )

        # Should not fail with "URL not configured" error
        assert "ArgoCD URL not configured" not in result.output
        assert result.exit_code == 0


# ── Snapshot mode ─────────────────────────────────────────────────────────────

class TestSnapshotMode:

    def test_healthy_exits_0(self, runner, mock_load_config):
        with patch("cli.commands.sync_cmd.get_app_status", return_value=HEALTHY_RESULT):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod"],
                env={"ARGOCD_TOKEN": "test-token"},
            )
        assert result.exit_code == 0
        assert "Synced" in result.output
        assert "Healthy" in result.output

    def test_healthy_shows_success_message(self, runner, mock_load_config):
        with patch("cli.commands.sync_cmd.get_app_status", return_value=HEALTHY_RESULT):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert "Synced + Healthy" in result.output

    def test_degraded_exits_1(self, runner, mock_load_config):
        with patch("cli.commands.sync_cmd.get_app_status", return_value=DEGRADED_RESULT):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert result.exit_code == 1
        assert "Degraded" in result.output

    def test_degraded_shows_debug_hints(self, runner, mock_load_config):
        """Degraded state prints kubectl debug commands."""
        with patch("cli.commands.sync_cmd.get_app_status", return_value=DEGRADED_RESULT):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert "kubectl" in result.output

    def test_out_of_sync_exits_0(self, runner, mock_load_config):
        """OutOfSync is transient — should not exit 1 in snapshot mode."""
        with patch("cli.commands.sync_cmd.get_app_status", return_value=OUT_OF_SYNC_RESULT):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert result.exit_code == 0

    def test_out_of_sync_shows_warning(self, runner, mock_load_config):
        with patch("cli.commands.sync_cmd.get_app_status", return_value=OUT_OF_SYNC_RESULT):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert "OutOfSync" in result.output

    def test_api_failure_exits_1(self, runner, mock_load_config):
        failed = SyncResult(success=False, app_name="guardops-app-prod", error_message="connection refused")
        with patch("cli.commands.sync_cmd.get_app_status", return_value=failed):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert result.exit_code == 1

    def test_argocd_ui_link_shown(self, runner, mock_load_config):
        """ArgoCD UI deep-link is printed so the operator can click through."""
        with patch("cli.commands.sync_cmd.get_app_status", return_value=HEALTHY_RESULT):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert "argocd.guardops.live" in result.output
        assert "guardops-app-prod" in result.output

    def test_app_name_from_config(self, runner, mock_load_config):
        """The ArgoCD app name is read from argocd.app_name_prod in config."""
        with patch("cli.commands.sync_cmd.get_app_status", return_value=HEALTHY_RESULT) as mock_status:
            runner.invoke(
                sync_status_command,
                ["--env", "prod"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        called_app_name = mock_status.call_args[0][0]
        assert called_app_name == "guardops-app-prod"

    def test_staging_app_name_from_config(self, runner, mock_load_config):
        staging_result = SyncResult(
            success=True, app_name="guardops-app-staging",
            sync_status="Synced", health_status="Healthy", revision="abc",
        )
        with patch("cli.commands.sync_cmd.get_app_status", return_value=staging_result) as mock_status:
            runner.invoke(
                sync_status_command,
                ["--env", "staging"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        called_app_name = mock_status.call_args[0][0]
        assert called_app_name == "guardops-app-staging"

    def test_revision_shown_in_table(self, runner, mock_load_config):
        with patch("cli.commands.sync_cmd.get_app_status", return_value=HEALTHY_RESULT):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert "abc1234" in result.output   # revision from HEALTHY_RESULT


# ── Wait mode ─────────────────────────────────────────────────────────────────

class TestWaitMode:

    def test_wait_exits_0_on_healthy(self, runner, mock_load_config):
        healthy_poll = SyncResult(
            success=True,
            app_name="guardops-app-prod",
            sync_status="Synced",
            health_status="Healthy",
            revision="abc",
            poll_duration_seconds=25.0,
        )
        with patch("cli.commands.sync_cmd.poll_until_healthy", return_value=healthy_poll):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod", "--wait", "--timeout", "300"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert result.exit_code == 0
        assert "Synced + Healthy" in result.output

    def test_wait_shows_poll_duration(self, runner, mock_load_config):
        healthy_poll = SyncResult(
            success=True, app_name="guardops-app-prod",
            sync_status="Synced", health_status="Healthy",
            revision="abc", poll_duration_seconds=42.0,
        )
        with patch("cli.commands.sync_cmd.poll_until_healthy", return_value=healthy_poll):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod", "--wait"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert "42s" in result.output

    def test_wait_exits_1_on_timeout(self, runner, mock_load_config):
        timed_out = SyncResult(
            success=False,
            app_name="guardops-app-prod",
            error_message="Timeout after 300s — app did not reach Healthy",
            poll_duration_seconds=300.0,
        )
        with patch("cli.commands.sync_cmd.poll_until_healthy", return_value=timed_out):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod", "--wait", "--timeout", "300"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert result.exit_code == 1
        assert "FAILED" in result.output or "Timeout" in result.output

    def test_wait_exits_1_on_degraded(self, runner, mock_load_config):
        degraded_poll = SyncResult(
            success=False,
            app_name="guardops-app-prod",
            error_message="Application entered Degraded health state",
            health_status="Degraded",
            poll_duration_seconds=30.0,
        )
        with patch("cli.commands.sync_cmd.poll_until_healthy", return_value=degraded_poll):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod", "--wait"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert result.exit_code == 1

    def test_wait_passes_timeout_to_poller(self, runner, mock_load_config):
        """--timeout value is forwarded to poll_until_healthy."""
        healthy_poll = SyncResult(
            success=True, app_name="guardops-app-prod",
            sync_status="Synced", health_status="Healthy",
            revision="x", poll_duration_seconds=1.0,
        )
        with patch("cli.commands.sync_cmd.poll_until_healthy", return_value=healthy_poll) as mock_poll:
            runner.invoke(
                sync_status_command,
                ["--env", "prod", "--wait", "--timeout", "120"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        called_timeout = mock_poll.call_args[1].get("timeout_seconds") or mock_poll.call_args[0][3]
        assert called_timeout == 120


# ── Config helper integration ─────────────────────────────────────────────────

class TestConfigHelperIntegration:

    def test_resolve_domain_shown_in_output(self, runner, mock_load_config):
        """The live URL (https://<domain>) should appear in the status table."""
        with patch("cli.commands.sync_cmd.get_app_status", return_value=HEALTHY_RESULT):
            result = runner.invoke(
                sync_status_command,
                ["--env", "prod"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert "guardops.live" in result.output

    def test_staging_domain_shown_for_staging(self, runner, mock_load_config):
        staging_healthy = SyncResult(
            success=True, app_name="guardops-app-staging",
            sync_status="Synced", health_status="Healthy", revision="x",
        )
        with patch("cli.commands.sync_cmd.get_app_status", return_value=staging_healthy):
            result = runner.invoke(
                sync_status_command,
                ["--env", "staging"],
                env={"ARGOCD_TOKEN": "tok"},
            )
        assert "staging.guardops.live" in result.output

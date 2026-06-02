"""
tests/test_history_cmd.py — Phase 12.

Tests `guardops history` via CliRunner against a real tmp SQLite DB. load_config
is patched (in _metadata_common, where open_store reads it) to point at the seeded
DB.
"""

import json
from unittest.mock import patch

import pytest

from backend.metadata.sqlite_store import SqliteMetadataStore
from cli.commands.history_cmd import history_command


@pytest.fixture
def seeded(tmp_path, make_report, make_finding):
    db = str(tmp_path / "guardops.db")
    store = SqliteMetadataStore(db)
    store.persist_report(
        make_report(project="demo", timestamp="2026-05-01T10:00:00",
                    findings=[make_finding(severity="HIGH", cve="CVE-A")]),
        environment="prod", git_sha="abc1234", source="deploy",
    )
    store.persist_report(
        make_report(project="demo", timestamp="2026-05-02T10:00:00",
                    findings=[make_finding(severity="LOW")]),
    )
    config = {"project": {"name": "demo"}, "metadata": {"backend": "sqlite", "path": db}}
    return config


def _patch_config(config):
    return patch("cli.commands._metadata_common.load_config", return_value=config)


def test_history_lists_runs(runner, seeded):
    # The Rich table cells are width-truncated under CliRunner (no TTY), so assert
    # on the untruncated header/count here; data content is covered by the JSON test.
    with _patch_config(seeded):
        result = runner.invoke(history_command, [])
    assert result.exit_code == 0
    assert "Scan History" in result.output
    assert "2 run(s)" in result.output


def test_history_empty_db(runner, tmp_path):
    config = {"metadata": {"path": str(tmp_path / "empty.db")}}
    with _patch_config(config):
        result = runner.invoke(history_command, [])
    assert result.exit_code == 0
    assert "No scan runs" in result.output


def test_history_json_output(runner, seeded):
    with _patch_config(seeded):
        result = runner.invoke(history_command, ["--json-output"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["project_name"] == "demo"
    assert any(r["git_sha"] == "abc1234" for r in data)


def test_history_limit(runner, seeded):
    with _patch_config(seeded):
        result = runner.invoke(history_command, ["--json-output", "-n", "1"])
    assert result.exit_code == 0
    assert len(json.loads(result.output)) == 1

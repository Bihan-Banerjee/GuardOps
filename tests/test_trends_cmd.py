"""
tests/test_trends_cmd.py — Phase 12.

Tests `guardops trends` per-day rollup output via CliRunner against a tmp DB.
"""

import json
from unittest.mock import patch

import pytest

from backend.metadata.sqlite_store import SqliteMetadataStore
from cli.commands.trends_cmd import trends_command


@pytest.fixture
def seeded(tmp_path, make_report, make_finding):
    db = str(tmp_path / "guardops.db")
    store = SqliteMetadataStore(db)
    store.persist_report(make_report(timestamp="2026-05-01T10:00:00", findings=[make_finding(severity="HIGH")]))
    store.persist_report(make_report(timestamp="2026-05-02T10:00:00", findings=[make_finding(severity="LOW")]))
    return {"metadata": {"path": db}}


def _patch_config(config):
    return patch("cli.commands._metadata_common.load_config", return_value=config)


def test_trends_table(runner, seeded):
    # Header is untruncated; the dated rows are asserted in the JSON test below.
    with _patch_config(seeded):
        result = runner.invoke(trends_command, ["--days", "36500"])
    assert result.exit_code == 0
    assert "Severity Trends" in result.output


def test_trends_json(runner, seeded):
    with _patch_config(seeded):
        result = runner.invoke(trends_command, ["--days", "36500", "--json-output"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
    assert {p["date"] for p in data} == {"2026-05-01", "2026-05-02"}


def test_trends_empty(runner, tmp_path):
    config = {"metadata": {"path": str(tmp_path / "empty.db")}}
    with _patch_config(config):
        result = runner.invoke(trends_command, [])
    assert result.exit_code == 0
    assert "No scan history" in result.output

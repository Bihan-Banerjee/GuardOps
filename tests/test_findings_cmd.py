"""
tests/test_findings_cmd.py — Phase 12.

Tests `guardops findings` filtering and output via CliRunner against a tmp DB.
"""

import json
from unittest.mock import patch

import pytest

from backend.metadata.sqlite_store import SqliteMetadataStore
from cli.commands.findings_cmd import findings_command


@pytest.fixture
def seeded(tmp_path, make_report, make_finding):
    db = str(tmp_path / "guardops.db")
    store = SqliteMetadataStore(db)
    store.persist_report(make_report(findings=[
        make_finding(severity="CRITICAL", cve="CVE-C", tool="trivy"),
        make_finding(severity="MEDIUM", cve="CVE-M", tool="semgrep"),
        make_finding(severity="LOW", cve="CVE-L", tool="bandit"),
    ]))
    return {"metadata": {"path": db}}


def _patch_config(config):
    return patch("cli.commands._metadata_common.load_config", return_value=config)


def test_findings_severity_threshold(runner, seeded):
    # Use JSON output so the assertion is width-independent (table cells truncate).
    with _patch_config(seeded):
        result = runner.invoke(findings_command, ["--severity", "HIGH", "--json-output"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {f["cve"] for f in data} == {"CVE-C"}  # only CRITICAL is >= HIGH


def test_findings_table_renders(runner, seeded):
    with _patch_config(seeded):
        result = runner.invoke(findings_command, [])
    assert result.exit_code == 0
    assert "Findings" in result.output


def test_findings_filter_by_tool(runner, seeded):
    with _patch_config(seeded):
        result = runner.invoke(findings_command, ["--tool", "semgrep", "--json-output"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["cve"] == "CVE-M"


def test_findings_filter_by_cve(runner, seeded):
    with _patch_config(seeded):
        result = runner.invoke(findings_command, ["--cve", "CVE-L", "--json-output"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["severity"] == "LOW"


def test_findings_empty_match(runner, seeded):
    with _patch_config(seeded):
        result = runner.invoke(findings_command, ["--cve", "CVE-NOPE"])
    assert result.exit_code == 0
    assert "No findings match" in result.output


def test_findings_invalid_severity_rejected(runner, seeded):
    with _patch_config(seeded):
        result = runner.invoke(findings_command, ["--severity", "BOGUS"])
    assert result.exit_code != 0  # click.Choice validation

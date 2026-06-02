"""
tests/test_diff_cmd.py — Phase 12.

Tests `guardops diff` regression detection and its CI exit codes:
  - new CRITICAL/HIGH finding → exit 1
  - identical findings → exit 0, "No change"
  - only a single run (no baseline) → exit 0, explanatory message
"""

from unittest.mock import patch


from backend.metadata.sqlite_store import SqliteMetadataStore
from cli.commands.diff_cmd import diff_command


def _patch_config(config):
    return patch("cli.commands._metadata_common.load_config", return_value=config)


def _seed(tmp_path):
    db = str(tmp_path / "guardops.db")
    return db, SqliteMetadataStore(db), {"metadata": {"path": db}}


def test_diff_new_high_exits_1(runner, tmp_path, make_report, make_finding):
    db, store, config = _seed(tmp_path)
    store.persist_report(make_report(project="demo", timestamp="2026-05-01T10:00:00",
                                     findings=[make_finding(severity="LOW", cve="CVE-OLD")]))
    store.persist_report(make_report(project="demo", timestamp="2026-05-02T10:00:00",
                                     findings=[make_finding(severity="HIGH", cve="CVE-NEW")]))
    with _patch_config(config):
        result = runner.invoke(diff_command, [])
    assert result.exit_code == 1
    assert "NEW" in result.output
    assert "Regression" in result.output


def test_diff_no_change_exits_0(runner, tmp_path, make_report, make_finding):
    db, store, config = _seed(tmp_path)
    # Identical finding in both runs → same fingerprint → no diff.
    common = dict(severity="HIGH", cve="CVE-SAME", tool="trivy", file_path="a.py", rule_id="R1")
    store.persist_report(make_report(project="demo", timestamp="2026-05-01T10:00:00",
                                     findings=[make_finding(**common)]))
    store.persist_report(make_report(project="demo", timestamp="2026-05-02T10:00:00",
                                     findings=[make_finding(**common)]))
    with _patch_config(config):
        result = runner.invoke(diff_command, [])
    assert result.exit_code == 0
    assert "No change" in result.output


def test_diff_fixed_only_exits_0(runner, tmp_path, make_report, make_finding):
    db, store, config = _seed(tmp_path)
    store.persist_report(make_report(project="demo", timestamp="2026-05-01T10:00:00",
                                     findings=[make_finding(severity="HIGH", cve="CVE-GONE")]))
    store.persist_report(make_report(project="demo", timestamp="2026-05-02T10:00:00",
                                     findings=[]))
    with _patch_config(config):
        result = runner.invoke(diff_command, [])
    assert result.exit_code == 0
    assert "FIXED" in result.output


def test_diff_single_run_no_baseline(runner, tmp_path, make_report, make_finding):
    db, store, config = _seed(tmp_path)
    store.persist_report(make_report(project="demo", findings=[make_finding(severity="HIGH")]))
    with _patch_config(config):
        result = runner.invoke(diff_command, [])
    assert result.exit_code == 0
    assert "no earlier run" in result.output.lower()


def test_diff_explicit_run_ids(runner, tmp_path, make_report, make_finding):
    db, store, config = _seed(tmp_path)
    r1 = store.persist_report(make_report(project="demo", timestamp="2026-05-01T10:00:00",
                                          findings=[make_finding(severity="LOW", cve="CVE-1")]))
    r2 = store.persist_report(make_report(project="demo", timestamp="2026-05-02T10:00:00",
                                          findings=[make_finding(severity="CRITICAL", cve="CVE-2")]))
    with _patch_config(config):
        result = runner.invoke(diff_command, ["--from", str(r1.run_id), "--to", str(r2.run_id), "--json-output"])
    assert result.exit_code == 1  # new CRITICAL
    import json
    data = json.loads(result.output)
    assert data["regressed"] is True
    assert len(data["new"]) == 1


def test_diff_missing_run_id_errors(runner, tmp_path, make_report, make_finding):
    db, store, config = _seed(tmp_path)
    store.persist_report(make_report(project="demo", findings=[make_finding()]))
    with _patch_config(config):
        result = runner.invoke(diff_command, ["--to", "999"])
    assert result.exit_code == 1
    assert "not found" in result.output

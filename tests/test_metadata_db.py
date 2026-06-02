"""
tests/test_metadata_db.py — Phase 12.

Tests the SQLite metadata store and the factory helpers directly against a real
on-disk database in tmp_path (SQLite ":memory:" is per-connection, so a file is
used to exercise the open-per-operation design).

Coverage:
  - schema auto-create + idempotency, nested-dir creation
  - persist round-trip (run + findings + tool_runs, severity mapping, blocked)
  - query_findings filters (severity threshold, tool, cve, run, image)
  - severity_trends day rollup
  - prune by keep_last / keep_days (+ findings cascade)
  - latest_run_before scoping
  - export_json shape
  - persist_report never raises (returns success=False)
  - factory: get_store backend selection, persist_report_safe enable/skip/swallow
"""

import os
import sqlite3
from datetime import datetime
from unittest.mock import patch

import pytest

from backend.metadata.base import NullMetadataStore
from backend.metadata.factory import get_store, persist_report_safe, resolve_db_path
from backend.metadata.sqlite_store import SqliteMetadataStore


@pytest.fixture
def db_path(tmp_path):
    # Nested path so we also exercise parent-dir creation.
    return str(tmp_path / "security" / "metadata" / "guardops.db")


@pytest.fixture
def store(db_path):
    return SqliteMetadataStore(db_path)


# ── schema ─────────────────────────────────────────────────────────────────────

def test_init_schema_idempotent_and_creates_dirs(store, db_path):
    store.init_schema()
    store.init_schema()  # second call must not error
    assert os.path.exists(db_path)
    assert store.list_runs() == []


# ── persist round-trip ─────────────────────────────────────────────────────────

def test_persist_round_trip(store, make_report, make_finding):
    findings = [
        make_finding(severity="CRITICAL", cve="CVE-1"),
        make_finding(severity="HIGH", cve="CVE-2"),
        make_finding(severity="LOW"),
    ]
    report = make_report(findings=findings, fail_on="HIGH")

    result = store.persist_report(report, environment="prod", git_sha="abc1234", source="deploy")
    assert result.success
    assert result.run_id is not None
    assert result.findings_written == 3

    runs = store.list_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run.project_name == "demo"
    assert run.environment == "prod"
    assert run.git_sha == "abc1234"
    assert run.source == "deploy"
    assert (run.crit_count, run.high_count, run.low_count) == (1, 1, 1)
    assert run.total_count == 3
    assert run.blocked is True  # HIGH finding with fail_on=HIGH

    assert len(store.query_findings(run_id=run.id)) == 3


def test_persist_records_tool_runs_in_export(store, make_report, make_finding):
    store.persist_report(make_report(tool="bandit", findings=[make_finding(tool="bandit")]))
    data = store.export_json()
    assert len(data["tool_runs"]) == 1
    assert data["tool_runs"][0]["tool"] == "bandit"


# ── query filters ──────────────────────────────────────────────────────────────

def test_query_findings_severity_threshold(store, make_report, make_finding):
    store.persist_report(make_report(findings=[
        make_finding(severity="CRITICAL", cve="CVE-C"),
        make_finding(severity="MEDIUM", cve="CVE-M"),
        make_finding(severity="LOW", cve="CVE-L"),
    ]))
    assert {f.severity for f in store.query_findings(severity="HIGH")} == {"CRITICAL"}
    assert {f.severity for f in store.query_findings(severity="MEDIUM")} == {"CRITICAL", "MEDIUM"}
    assert len(store.query_findings(severity="LOW")) == 3


def test_query_findings_tool_cve_image(store, make_report, make_finding):
    store.persist_report(make_report(
        image="demo:v1",
        tool="trivy",
        findings=[make_finding(tool="trivy", cve="CVE-X", severity="HIGH")],
    ))
    assert len(store.query_findings(cve="CVE-X")) == 1
    assert len(store.query_findings(cve="CVE-NONE")) == 0
    assert len(store.query_findings(tool="trivy")) == 1
    assert len(store.query_findings(tool="bandit")) == 0
    assert len(store.query_findings(image="demo:v1")) == 1
    assert len(store.query_findings(image="other:v9")) == 0


# ── trends ─────────────────────────────────────────────────────────────────────

def test_severity_trends_groups_by_day(store, make_report, make_finding):
    store.persist_report(make_report(timestamp="2026-05-01T10:00:00", findings=[make_finding(severity="HIGH")]))
    store.persist_report(make_report(timestamp="2026-05-01T18:00:00", findings=[make_finding(severity="LOW")]))
    store.persist_report(make_report(timestamp="2026-05-02T09:00:00", findings=[make_finding(severity="CRITICAL")]))

    by_day = {p.date: p for p in store.severity_trends(days=36500)}
    assert by_day["2026-05-01"].runs == 2
    assert by_day["2026-05-01"].high == 1
    assert by_day["2026-05-01"].low == 1
    assert by_day["2026-05-02"].crit == 1


# ── prune ──────────────────────────────────────────────────────────────────────

def test_prune_keep_last_cascades_findings(store, make_report, make_finding):
    for i in range(5):
        store.persist_report(make_report(
            timestamp=f"2026-05-0{i + 1}T10:00:00",
            findings=[make_finding(severity="HIGH", cve=f"CVE-{i}")],
        ))
    assert len(store.list_runs(limit=100)) == 5

    res = store.prune(keep_last=2)
    assert res.success
    assert res.runs_deleted == 3
    assert res.findings_deleted == 3

    remaining = store.list_runs(limit=100)
    assert len(remaining) == 2
    total = sum(len(store.query_findings(run_id=r.id)) for r in remaining)
    assert total == 2


def test_prune_keep_days(store, make_report, make_finding):
    store.persist_report(make_report(timestamp="2000-01-01T00:00:00", findings=[make_finding(severity="LOW")]))
    store.persist_report(make_report(timestamp=datetime.utcnow().isoformat(), findings=[make_finding(severity="HIGH")]))

    res = store.prune(keep_days=30)
    assert res.success
    assert res.runs_deleted == 1
    assert len(store.list_runs(limit=100)) == 1


def test_prune_noop_when_no_policy(store, make_report):
    store.persist_report(make_report())
    res = store.prune(keep_days=None, keep_last=None)
    assert res.success
    assert res.runs_deleted == 0
    assert len(store.list_runs()) == 1


# ── latest_run_before ──────────────────────────────────────────────────────────

def test_latest_run_before_scopes_to_project(store, make_report):
    r1 = store.persist_report(make_report(project="demo", timestamp="2026-05-01T10:00:00"))
    r2 = store.persist_report(make_report(project="demo", timestamp="2026-05-02T10:00:00"))
    r3 = store.persist_report(make_report(project="other", timestamp="2026-05-03T10:00:00"))

    prev = store.latest_run_before(run_id=r2.run_id, project="demo")
    assert prev.id == r1.run_id

    prev_for_r3 = store.latest_run_before(run_id=r3.run_id, project="demo")
    assert prev_for_r3.id == r2.run_id


# ── export ─────────────────────────────────────────────────────────────────────

def test_export_json_shape(store, make_report, make_finding):
    store.persist_report(make_report(findings=[make_finding(severity="HIGH", cve="CVE-Z")]))
    data = store.export_json()
    assert data["schema_version"] == "1"
    assert len(data["runs"]) == 1
    assert len(data["findings"]) == 1
    assert data["findings"][0]["cve"] == "CVE-Z"
    assert len(data["tool_runs"]) == 1


# ── non-fatal guarantee ────────────────────────────────────────────────────────

def test_persist_report_never_raises(store, make_report):
    with patch.object(store, "_connect", side_effect=sqlite3.OperationalError("boom")):
        result = store.persist_report(make_report())
    assert result.success is False
    assert "boom" in result.error_message


# ── factory ────────────────────────────────────────────────────────────────────

def test_get_store_sqlite(tmp_path):
    config = {"metadata": {"backend": "sqlite", "path": str(tmp_path / "g.db")}}
    assert isinstance(get_store(config), SqliteMetadataStore)


def test_get_store_unknown_backend_is_null(tmp_path):
    assert isinstance(get_store({"metadata": {"backend": "postgres"}}), NullMetadataStore)


def test_resolve_db_path_is_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = resolve_db_path({"metadata": {"path": "security/metadata/guardops.db"}})
    assert os.path.isabs(p)
    assert p.endswith("guardops.db")


def test_persist_report_safe_disabled_returns_none(make_report):
    assert persist_report_safe(make_report(), {"metadata": {"enabled": False}}) is None


def test_persist_report_safe_writes(tmp_path, make_report, make_finding):
    db = str(tmp_path / "g.db")
    config = {"metadata": {"enabled": True, "backend": "sqlite", "path": db}}
    res = persist_report_safe(
        make_report(findings=[make_finding(severity="HIGH")]),
        config, environment="prod", git_sha="deadbee", source="scan",
    )
    assert res is not None and res.success
    runs = SqliteMetadataStore(db).list_runs()
    assert len(runs) == 1
    assert runs[0].git_sha == "deadbee"


def test_persist_report_safe_swallows_store_errors(make_report):
    config = {"metadata": {"enabled": True, "backend": "sqlite", "path": "unused.db"}}
    with patch("backend.metadata.factory.get_store") as mock_get:
        mock_get.return_value.persist_report.side_effect = RuntimeError("kaboom")
        result = persist_report_safe(make_report(), config)  # must not raise
    assert result is None

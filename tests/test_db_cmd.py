"""
tests/test_db_cmd.py — Phase 12.

Tests the `guardops db` group: init, prune, export.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch


from backend.metadata.sqlite_store import SqliteMetadataStore
from cli.commands.db_cmd import db_group


def _patch_config(config):
    return patch("cli.commands._metadata_common.load_config", return_value=config)


def test_db_init_creates_file(runner, tmp_path):
    db = str(tmp_path / "sub" / "guardops.db")
    config = {"metadata": {"backend": "sqlite", "path": db}}
    with _patch_config(config):
        result = runner.invoke(db_group, ["init"])
    assert result.exit_code == 0
    assert os.path.exists(db)


def test_db_prune_keep_last(runner, tmp_path, make_report, make_finding):
    db = str(tmp_path / "guardops.db")
    store = SqliteMetadataStore(db)
    for i in range(4):
        store.persist_report(make_report(timestamp=f"2026-05-0{i + 1}T10:00:00",
                                         findings=[make_finding(severity="LOW")]))
    config = {"metadata": {"path": db}}
    with _patch_config(config):
        result = runner.invoke(db_group, ["prune", "--keep-last", "1"])
    assert result.exit_code == 0
    assert "Pruned" in result.output
    assert len(store.list_runs(limit=100)) == 1


def test_db_prune_uses_config_retention(runner, tmp_path, make_report, make_finding):
    db = str(tmp_path / "guardops.db")
    store = SqliteMetadataStore(db)
    for i in range(3):
        store.persist_report(make_report(timestamp=f"2026-05-0{i + 1}T10:00:00",
                                         findings=[make_finding(severity="LOW")]))
    config = {"metadata": {"path": db, "retention_keep_last": 1, "retention_days": 0}}
    with _patch_config(config):
        result = runner.invoke(db_group, ["prune"])  # no flags → config policy
    assert result.exit_code == 0
    assert len(store.list_runs(limit=100)) == 1


def test_db_prune_no_policy_is_noop(runner, tmp_path, make_report):
    db = str(tmp_path / "guardops.db")
    store = SqliteMetadataStore(db)
    store.persist_report(make_report())
    config = {"metadata": {"path": db, "retention_keep_last": 0, "retention_days": 0}}
    with _patch_config(config):
        result = runner.invoke(db_group, ["prune"])
    assert result.exit_code == 0
    assert "No retention configured" in result.output
    assert len(store.list_runs()) == 1


def test_db_export_to_file(runner, tmp_path, make_report, make_finding):
    db = str(tmp_path / "guardops.db")
    store = SqliteMetadataStore(db)
    store.persist_report(make_report(findings=[make_finding(severity="HIGH", cve="CVE-E")]))
    out = str(tmp_path / "dump.json")
    config = {"metadata": {"path": db}}
    with _patch_config(config):
        result = runner.invoke(db_group, ["export", "-o", out])
    assert result.exit_code == 0
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert len(data["runs"]) == 1
    assert data["findings"][0]["cve"] == "CVE-E"


def test_db_export_stdout(runner, tmp_path, make_report, make_finding):
    db = str(tmp_path / "guardops.db")
    store = SqliteMetadataStore(db)
    store.persist_report(make_report(findings=[make_finding(severity="HIGH")]))
    config = {"metadata": {"path": db}}
    with _patch_config(config):
        result = runner.invoke(db_group, ["export"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "runs" in data and len(data["runs"]) == 1


def test_db_export_to_s3(runner, tmp_path, make_report, make_finding):
    db = str(tmp_path / "guardops.db")
    SqliteMetadataStore(db).persist_report(
        make_report(findings=[make_finding(severity="HIGH")])
    )
    config = {
        "project": {"name": "demo"},
        "metadata": {"path": db, "s3_bucket": "bkt", "s3_prefix": "metadata"},
    }
    captured = {}

    class _Fake:
        def put_object(self, Bucket, Key, Body, **kw):
            captured.update(bucket=Bucket, key=Key, body=Body)
            return {}

    with _patch_config(config), \
         patch("backend.metadata.s3_store._s3_client", return_value=_Fake()):
        result = runner.invoke(db_group, ["export", "--to-s3"])
    assert result.exit_code == 0
    assert captured["bucket"] == "bkt"
    assert captured["key"] == "metadata/demo/latest.json"
    assert len(json.loads(captured["body"])["runs"]) == 1


def test_db_export_to_s3_no_bucket_fails(runner, tmp_path, make_report):
    db = str(tmp_path / "guardops.db")
    SqliteMetadataStore(db).persist_report(make_report())
    config = {"project": {"name": "demo"}, "metadata": {"path": db}}  # no s3_bucket
    with _patch_config(config):
        result = runner.invoke(db_group, ["export", "--to-s3"])
    assert result.exit_code == 1
    assert "No S3 bucket" in result.output

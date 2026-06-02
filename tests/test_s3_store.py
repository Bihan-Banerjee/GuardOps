"""
tests/test_s3_store.py — Phase 13.

The S3 metadata bridge that feeds the dashboard. A tiny in-memory fake S3 client is
injected so the tests need neither moto nor a live bucket. The core guarantee: an
S3MetadataStore hydrated from export_json() returns the same reads as the source
SqliteMetadataStore (no SQL is re-implemented — it delegates).
"""

import io

import pytest

from backend.metadata.sqlite_store import SqliteMetadataStore
from backend.metadata.s3_store import (
    S3MetadataStore,
    build_s3_key,
    put_export_to_s3,
)


class _NoSuchKey(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "NoSuchKey"}}
        super().__init__("NoSuchKey")


class FakeS3:
    """Minimal stand-in for boto3's S3 client: get_object / put_object."""
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[(Bucket, Key)] = Body
        return {}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise _NoSuchKey()
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


@pytest.fixture
def seeded_sqlite(tmp_path, make_report, make_finding):
    """A SqliteMetadataStore with two runs across two projects/severities."""
    store = SqliteMetadataStore(str(tmp_path / "src.db"))
    store.persist_report(
        make_report(project="demo", timestamp="2026-05-20T10:00:00",
                    findings=[make_finding(severity="HIGH", tool="trivy", cve="CVE-1"),
                              make_finding(severity="LOW", tool="bandit")]),
        environment="prod", git_sha="aaa1111",
    )
    store.persist_report(
        make_report(project="demo", timestamp="2026-05-21T10:00:00",
                    findings=[make_finding(severity="CRITICAL", tool="semgrep")]),
        environment="staging", git_sha="bbb2222",
    )
    return store


def test_build_s3_key():
    assert build_s3_key("metadata", "demo") == "metadata/demo/latest.json"
    assert build_s3_key("/m/", "/demo/") == "m/demo/latest.json"
    assert build_s3_key("metadata", "") == "metadata/latest.json"


def test_roundtrip_matches_source(seeded_sqlite):
    fake = FakeS3()
    key = build_s3_key("metadata", "demo")
    put_export_to_s3(seeded_sqlite.export_json(), "bkt", key, client=fake)

    s3 = S3MetadataStore("bkt", key=key, client=fake)

    src_runs = seeded_sqlite.list_runs(limit=100)
    s3_runs = s3.list_runs(limit=100)
    assert [r.to_dict() for r in s3_runs] == [r.to_dict() for r in src_runs]

    # run_id links survive hydration
    top = s3_runs[0]
    src_f = seeded_sqlite.query_findings(run_id=top.id, limit=100)
    s3_f = s3.query_findings(run_id=top.id, limit=100)
    assert [f.to_dict() for f in s3_f] == [f.to_dict() for f in src_f]

    # filters delegate correctly
    highs = s3.query_findings(severity="HIGH", limit=100)
    assert all(f.severity in ("HIGH", "CRITICAL") for f in highs)
    assert s3.query_findings(cve="CVE-1", limit=100)[0].cve == "CVE-1"


def test_environment_filter_and_trends(seeded_sqlite):
    fake = FakeS3()
    key = build_s3_key("metadata", "demo")
    put_export_to_s3(seeded_sqlite.export_json(), "bkt", key, client=fake)
    s3 = S3MetadataStore("bkt", key=key, client=fake)

    assert len(s3.list_runs(environment="prod", limit=100)) == 1
    assert s3.severity_trends(days=3650) == seeded_sqlite.severity_trends(days=3650)


def test_missing_object_is_empty_not_error():
    fake = FakeS3()  # nothing uploaded
    s3 = S3MetadataStore("bkt", key="metadata/none/latest.json", client=fake)
    assert s3.list_runs(limit=10) == []
    assert s3.query_findings(limit=10) == []


def test_writes_are_no_ops(seeded_sqlite, make_report):
    fake = FakeS3()
    put_export_to_s3(seeded_sqlite.export_json(), "bkt", "k", client=fake)
    s3 = S3MetadataStore("bkt", key="k", client=fake)

    persist = s3.persist_report(make_report())
    assert persist.success is False and persist.skipped is True

    prune = s3.prune(keep_last=1)
    assert prune.success is False


def test_requires_bucket():
    with pytest.raises(ValueError):
        S3MetadataStore("")


def test_from_config():
    config = {
        "project": {"name": "demo"},
        "metadata": {"s3_bucket": "bkt", "s3_prefix": "meta", "region": "ap-south-1"},
    }
    store = S3MetadataStore.from_config(config)
    assert store.bucket == "bkt"
    assert store.key == "meta/demo/latest.json"
    assert store.region == "ap-south-1"

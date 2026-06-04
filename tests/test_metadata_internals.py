"""
tests/test_metadata_internals.py — backend/metadata base + factory uncovered branches.

The NullMetadataStore no-ops, dataclass to_dict, and the factory's s3 / unknown-backend
/ exception-handling paths.
"""

from backend.metadata import factory
from backend.metadata.base import NullMetadataStore, PersistResult, PruneResult


def _raise(*a, **k):
    raise Exception("boom")


def test_null_store_all_methods():
    s = NullMetadataStore("reason")
    assert s.init_schema() is None
    assert s.persist_report(None).success is False
    assert s.list_runs() == []
    assert s.get_run(1) is None
    assert s.latest_run_before() is None
    assert s.query_findings() == []
    assert s.severity_trends() == []
    assert s.prune().success is False
    assert s.export_json()["runs"] == []


def test_dataclass_to_dict():
    assert PersistResult(success=True).to_dict()["success"] is True
    assert PruneResult(success=True).to_dict()["success"] is True


def test_get_store_sqlite(tmp_path):
    store = factory.get_store({"metadata": {"backend": "sqlite", "path": str(tmp_path / "g.db")}})
    assert store is not None


def test_get_store_s3(monkeypatch):
    sentinel = object()
    monkeypatch.setattr("backend.metadata.s3_store.S3MetadataStore.from_config",
                        classmethod(lambda cls, c: sentinel))
    store = factory.get_store({"metadata": {"backend": "s3", "s3_bucket": "b"}, "project": {"name": "p"}})
    assert store is sentinel


def test_get_store_s3_error_returns_null(monkeypatch):
    monkeypatch.setattr("backend.metadata.s3_store.S3MetadataStore.from_config",
                        classmethod(_raise))
    assert isinstance(factory.get_store({"metadata": {"backend": "s3"}}), NullMetadataStore)


def test_get_store_unknown_backend():
    assert isinstance(factory.get_store({"metadata": {"backend": "mongodb"}}), NullMetadataStore)


def test_persist_report_safe_disabled():
    assert factory.persist_report_safe(None, {"metadata": {"enabled": False}}) is None


def test_persist_report_safe_swallows_exception(monkeypatch):
    monkeypatch.setattr(factory, "get_store", _raise)
    assert factory.persist_report_safe(object(), {}) is None   # never raises


def test_emit_swallows_output_error():
    factory._emit(_raise, "msg")    # must not propagate

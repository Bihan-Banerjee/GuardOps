"""
tests/test_remaining_branches.py — app routes, pusher tag-latest, s3_store reads.
"""

import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.dashboard.app import create_app
from backend.dashboard.settings import DashboardSettings
from backend.pipeline import pusher
from backend.metadata.s3_store import S3MetadataStore, put_export_to_s3, _s3_client


def _settings():
    return DashboardSettings(
        project_name="x", config={"metadata": {"backend": "null"}},   # NullMetadataStore → no DB file
        prometheus_url="", loki_url="", argocd_url="", argocd_token="",
        auth_mode="none", auth_token="", basic_user="", basic_password="")


# ── dashboard app routes ──────────────────────────────────────────────────────

def test_root_readyz_and_metrics_routes():
    client = TestClient(create_app(_settings()))
    assert client.get("/").status_code == 200
    assert client.get("/readyz").status_code == 200
    assert client.get("/api/v1/metrics/resources").status_code == 200   # auth disabled


def test_readyz_degraded_when_store_errors():
    app = create_app(_settings())

    def boom(*a, **k):
        raise Exception("db error")
    app.state.store = SimpleNamespace(list_runs=boom)
    assert TestClient(app).get("/readyz").json()["status"] == "degraded"


# ── pusher tag-latest + no-colon ──────────────────────────────────────────────

def test_push_no_colon_defaults_to_latest(monkeypatch):
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    with patch("backend.pipeline.pusher._ensure_ecr_repository", return_value=True), \
         patch("backend.pipeline.pusher._authenticate_docker_to_ecr", return_value=True), \
         patch("backend.pipeline.pusher._tag_and_push_latest", return_value=True), \
         patch("backend.pipeline.pusher.run_command", return_value=MagicMock(returncode=0)):
        r = pusher.push_to_ecr("nocolon", {})
    assert r.tag == "latest"


def test_tag_and_push_latest():
    with patch("backend.pipeline.pusher.run_command", return_value=MagicMock(returncode=0)):
        assert pusher._tag_and_push_latest("app:t", "reg", "app") is True
    with patch("backend.pipeline.pusher.run_command", return_value=MagicMock(returncode=1)):
        assert pusher._tag_and_push_latest("app:t", "reg", "app") is False


# ── s3_store ──────────────────────────────────────────────────────────────────

class _FakeS3:
    def __init__(self, body):
        self._body = body
        self.put = None

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self._body)}

    def put_object(self, **kw):
        self.put = kw


class _NotFoundS3:
    def get_object(self, Bucket, Key):
        exc = Exception()
        exc.response = {"Error": {"Code": "NoSuchKey"}}
        raise exc


def test_s3_client_returns_injected():
    sentinel = object()
    assert _s3_client("us-east-1", sentinel) is sentinel


def test_put_export_to_s3():
    fake = _FakeS3(b"")
    uri = put_export_to_s3({"runs": []}, "bucket", "key", client=fake)
    assert uri == "s3://bucket/key" and fake.put["Bucket"] == "bucket"


def test_s3_store_delegated_reads():
    export = {"runs": [], "findings": [], "tool_runs": []}
    store = S3MetadataStore(bucket="b", client=_FakeS3(json.dumps(export).encode()))
    assert store.list_runs() == []
    assert store.get_run(1) is None
    assert store.latest_run_before() is None
    assert store.query_findings() == []
    assert store.severity_trends() == []
    assert isinstance(store.export_json(), dict)
    # write no-ops
    assert store.persist_report(None).skipped is True
    assert store.prune().success is False
    assert store.init_schema() is None


def test_s3_store_missing_object_is_empty():
    store = S3MetadataStore(bucket="b", client=_NotFoundS3())
    assert store.list_runs() == []

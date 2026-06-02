"""
tests/test_dashboard_auth.py — Phase 13.

The shared-credential gate. Findings reveal real vulnerabilities, so /api routes
must reject unauthenticated requests when a credential is configured, while the
health probes stay open.
"""

import pytest

pytest.importorskip("fastapi")

from starlette.testclient import TestClient  # noqa: E402

from backend.metadata.sqlite_store import SqliteMetadataStore  # noqa: E402
from backend.dashboard.app import create_app  # noqa: E402
from backend.dashboard.settings import DashboardSettings  # noqa: E402


def _settings(tmp_path, **overrides):
    db = str(tmp_path / "auth.db")
    SqliteMetadataStore(db).init_schema()
    config = {"project": {"name": "demo"}, "metadata": {"backend": "sqlite", "path": db}}
    base = dict(
        project_name="demo", config=config,
        prometheus_url="", loki_url="", argocd_url="", argocd_token="",
        auth_mode="token", auth_token="", basic_user="", basic_password="",
        cors_origins=[],
    )
    base.update(overrides)
    return DashboardSettings(**base)


def test_token_mode_rejects_missing_and_wrong(tmp_path):
    client = TestClient(create_app(_settings(tmp_path, auth_mode="token", auth_token="s3cret")))
    # missing credential → 401/403 depending on FastAPI version
    assert client.get("/api/v1/meta").status_code in (401, 403)
    # wrong token → 401
    assert client.get("/api/v1/meta", headers={"Authorization": "Bearer nope"}).status_code == 401
    # correct token → 200
    ok = client.get("/api/v1/meta", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert ok.json()["auth_enabled"] is True


def test_basic_mode(tmp_path):
    client = TestClient(create_app(_settings(
        tmp_path, auth_mode="basic", basic_user="admin", basic_password="pw",
    )))
    assert client.get("/api/v1/meta").status_code in (401, 403)
    assert client.get("/api/v1/meta", auth=("admin", "wrong")).status_code == 401
    assert client.get("/api/v1/meta", auth=("admin", "pw")).status_code == 200


def test_health_is_open_even_with_auth(tmp_path):
    client = TestClient(create_app(_settings(tmp_path, auth_mode="token", auth_token="s3cret")))
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_no_credential_configured_is_open(tmp_path):
    # auth_mode=token but no token set → open (local dev), reported via meta
    client = TestClient(create_app(_settings(tmp_path, auth_mode="token", auth_token="")))
    body = client.get("/api/v1/meta")
    assert body.status_code == 200
    assert body.json()["auth_enabled"] is False

"""
tests/test_dashboard_api.py — Phase 13.

Exercises every dashboard route against a seeded SQLite store via FastAPI's
TestClient. Live sources (Prometheus/Loki/ArgoCD/kubectl) are configured empty /
patched off, so the "available: false" graceful-degradation path is what's asserted
— no live cluster required.
"""

import pytest

pytest.importorskip("fastapi")

from starlette.testclient import TestClient  # noqa: E402

from backend.metadata.sqlite_store import SqliteMetadataStore  # noqa: E402
from backend.dashboard.app import create_app  # noqa: E402
from backend.dashboard.settings import DashboardSettings  # noqa: E402


@pytest.fixture
def client(tmp_path, make_report, make_finding):
    db = str(tmp_path / "dash.db")
    store = SqliteMetadataStore(db)
    # Run #1 — one HIGH finding (CVE-1)
    store.persist_report(
        make_report(project="demo", image="demo:1", timestamp="2026-05-20T10:00:00",
                    findings=[make_finding(severity="HIGH", cve="CVE-1", tool="trivy")]),
        environment="prod", git_sha="aaa1111",
    )
    # Run #2 — the same HIGH plus a NEW CRITICAL (a regression vs #1)
    store.persist_report(
        make_report(project="demo", image="demo:2", timestamp="2026-05-21T10:00:00",
                    findings=[make_finding(severity="HIGH", cve="CVE-1", tool="trivy"),
                              make_finding(severity="CRITICAL", tool="semgrep", rule_id="R2")]),
        environment="prod", git_sha="bbb2222",
    )
    config = {"project": {"name": "demo"}, "metadata": {"backend": "sqlite", "path": db}}
    settings = DashboardSettings(
        project_name="demo", config=config,
        prometheus_url="", loki_url="", argocd_url="", argocd_token="",
        auth_mode="none", auth_token="", basic_user="", basic_password="",
        cors_origins=[],
    )
    return TestClient(create_app(settings))


# ── findings / durable routes ────────────────────────────────────────────────

def test_meta(client):
    body = client.get("/api/v1/meta").json()
    assert body["project"] == "demo"
    assert body["metadata_backend"] == "sqlite"
    assert body["auth_enabled"] is False
    assert body["sources"] == {"prometheus": False, "loki": False, "argocd": False}


def test_summary(client):
    body = client.get("/api/v1/summary").json()
    assert body["total_runs"] == 2
    assert body["latest"]["crit_count"] == 1
    assert body["gate_pass_rate"] is not None
    tools = {t["tool"] for t in body["by_tool"]}
    assert {"trivy", "semgrep"} <= tools


def test_runs_and_detail(client):
    runs = client.get("/api/v1/runs").json()["runs"]
    assert len(runs) == 2
    top_id = runs[0]["id"]
    detail = client.get(f"/api/v1/runs/{top_id}").json()
    assert detail["run"]["id"] == top_id
    assert len(detail["findings"]) == 2


def test_run_detail_404(client):
    assert client.get("/api/v1/runs/99999").status_code == 404


def test_findings_filters(client):
    high = client.get("/api/v1/findings", params={"severity": "HIGH"}).json()["findings"]
    assert all(f["severity"] in ("HIGH", "CRITICAL") for f in high)
    cve = client.get("/api/v1/findings", params={"cve": "CVE-1"}).json()["findings"]
    assert cve and all(f["cve"] == "CVE-1" for f in cve)


def test_trends(client):
    body = client.get("/api/v1/trends", params={"days": 3650}).json()
    assert body["days"] == 3650
    assert len(body["points"]) >= 1


def test_diff_detects_regression(client):
    body = client.get("/api/v1/diff").json()
    assert body["regressed"] is True
    assert body["new_blocking"] >= 1
    assert any(f["severity"] == "CRITICAL" for f in body["new"])


def test_diff_run_not_found(client):
    assert client.get("/api/v1/diff", params={"to_id": 99999}).status_code == 404


def test_export(client):
    body = client.get("/api/v1/export").json()
    assert len(body["runs"]) == 2
    assert "findings" in body


# ── live routes degrade gracefully ───────────────────────────────────────────

def test_metrics_app_unavailable(client):
    body = client.get("/api/v1/metrics/app").json()
    assert body["available"] is False  # prometheus_url empty


def test_runtime_alerts_unavailable(client):
    body = client.get("/api/v1/runtime/alerts").json()
    assert body["available"] is False  # loki_url empty


def test_sync_status_unavailable(client):
    body = client.get("/api/v1/sync-status", params={"env": "prod"}).json()
    assert body["available"] is False  # argocd not configured


def test_quarantine_unavailable_without_kubectl(client, monkeypatch):
    monkeypatch.setattr("backend.dashboard.sources.quarantine.shutil.which", lambda _: None)
    body = client.get("/api/v1/quarantine").json()
    assert body["available"] is False
    assert "kubectl" in body["reason"]

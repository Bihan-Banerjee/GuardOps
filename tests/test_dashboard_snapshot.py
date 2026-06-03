"""
tests/test_dashboard_snapshot.py — v1.0.0.

The dashboard snapshot (backend/dashboard/snapshot.py) is what makes the public SPA
work 24/7 while the cluster is down. These tests pin its contract:

  - the envelope carries generated_at + version + a `data` map keyed by API path,
  - every route create_app() mounts has a snapshot entry (so the frontend fallback
    can never miss one),
  - durable scan data flows through from the MetadataStore,
  - live sources degrade to {"available": False} instead of raising, and
  - a failing source never voids the whole snapshot.
"""

from datetime import datetime

import pytest

from cli import __version__
from backend.dashboard import snapshot as snap_mod
from backend.dashboard.snapshot import build_snapshot
from backend.dashboard.settings import DashboardSettings
from backend.metadata.sqlite_store import SqliteMetadataStore

# Every path the SPA fetches — keep in lockstep with backend/dashboard/app.py routes
# the frontend calls (web/src/api.js).
EXPECTED_ROUTES = {
    "/api/v1/meta",
    "/api/v1/summary",
    "/api/v1/runs",
    "/api/v1/findings",
    "/api/v1/trends",
    "/api/v1/metrics/app",
    "/api/v1/metrics/resources",
    "/api/v1/runtime/alerts",
    "/api/v1/quarantine",
    "/api/v1/sync-status",
}


def _offline_settings():
    """Settings with no live sources, so metrics/runtime/sync degrade gracefully."""
    return DashboardSettings(
        project_name="demo",
        config={"metadata": {"backend": "sqlite"}, "project": {"name": "demo"}},
        prometheus_url="",
        loki_url="",
        argocd_url="",
        argocd_token="",
        auth_mode="none",
        auth_token="",
        basic_user="",
        basic_password="",
    )


@pytest.fixture
def seeded_store(tmp_path, make_report, make_finding):
    """A SQLite store with one persisted run (a HIGH trivy finding)."""
    store = SqliteMetadataStore(str(tmp_path / "guardops.db"))
    store.init_schema()
    report = make_report(findings=[make_finding(severity="HIGH", tool="trivy", cve="CVE-2026-1")])
    store.persist_report(report, environment="staging", git_sha="abc123", source="scan")
    return store


@pytest.fixture(autouse=True)
def _no_kubectl(monkeypatch):
    """Keep the quarantine source hermetic — never shell out to kubectl in tests."""
    monkeypatch.setattr(
        snap_mod.quarantine_src, "quarantine_status",
        lambda *a, **k: {"available": False, "reason": "kubectl stubbed in tests"},
    )


def test_envelope_shape_and_all_routes(seeded_store):
    snapshot = build_snapshot(_offline_settings(), store=seeded_store)

    assert snapshot["version"] == __version__
    assert snapshot["project"] == "demo"
    # generated_at must be ISO-8601 parseable
    datetime.fromisoformat(snapshot["generated_at"])

    assert set(snapshot["data"].keys()) == EXPECTED_ROUTES


def test_durable_data_flows_through(seeded_store):
    data = build_snapshot(_offline_settings(), store=seeded_store)["data"]

    assert data["/api/v1/summary"]["total_runs"] == 1
    assert data["/api/v1/summary"]["by_severity"]["HIGH"] == 1
    assert len(data["/api/v1/runs"]["runs"]) == 1
    assert data["/api/v1/runs"]["runs"][0]["environment"] == "staging"
    assert any(f["cve"] == "CVE-2026-1" for f in data["/api/v1/findings"]["findings"])
    assert data["/api/v1/trends"]["days"] == 30


def test_live_sources_degrade_not_raise(seeded_store):
    data = build_snapshot(_offline_settings(), store=seeded_store)["data"]

    for route in ("/api/v1/metrics/app", "/api/v1/runtime/alerts",
                  "/api/v1/quarantine", "/api/v1/sync-status"):
        assert data[route]["available"] is False


def test_live_source_payloads_do_not_leak_internal_details(seeded_store, monkeypatch):
    # The snapshot is published to a PUBLIC object — a live source returning a raw
    # connection error (EKS endpoint, in-cluster DNS) or a target URL must be
    # scrubbed before it lands in the snapshot.
    leaky = {
        "available": False,
        "reason": "dial tcp: lookup ABC123.gr7.ap-south-1.eks.amazonaws.com: no such host",
        "prometheus_url": "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090",
    }
    monkeypatch.setattr(snap_mod.metrics_src, "app_metrics", lambda *a, **k: leaky)

    blob = build_snapshot(_offline_settings(), store=seeded_store)
    text = __import__("json").dumps(blob)

    assert "eks.amazonaws.com" not in text
    assert "svc.cluster.local" not in text
    metrics = blob["data"]["/api/v1/metrics/app"]
    assert metrics["available"] is False
    assert "prometheus_url" not in metrics
    assert metrics["reason"] == "live source unavailable while the cluster is offline"


def test_a_failing_source_does_not_void_the_snapshot(seeded_store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("store exploded")

    monkeypatch.setattr(snap_mod.findings_src, "compute_summary", boom)
    snapshot = build_snapshot(_offline_settings(), store=seeded_store)

    # Summary falls back to the empty default (annotated with the error)...
    assert snapshot["data"]["/api/v1/summary"]["total_runs"] == 0
    assert "store exploded" in snapshot["data"]["/api/v1/summary"]["_error"]
    # ...and the rest of the snapshot is still intact.
    assert len(snapshot["data"]["/api/v1/runs"]["runs"]) == 1

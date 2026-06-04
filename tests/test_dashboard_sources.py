"""
tests/test_dashboard_sources.py — backend/dashboard/sources/*.

The four live data providers. Each must return {"available": False, ...} (never raise)
when its source is unconfigured/unreachable, and {"available": True, ...} with data
otherwise. Prometheus/Loki/ArgoCD/kubectl are all mocked.
"""

from types import SimpleNamespace

from backend.dashboard.sources import sync as sync_src
from backend.dashboard.sources import metrics as metrics_src
from backend.dashboard.sources import quarantine as q_src
from backend.dashboard.sources import runtime as rt_src


def _settings(**kw):
    base = dict(prometheus_url="", loki_url="", argocd_url="", argocd_token="",
                config={"project": {"name": "demo"}})
    base.update(kw)
    return SimpleNamespace(**base)


# ── sync (ArgoCD) ─────────────────────────────────────────────────────────────

def test_sync_unconfigured_url():
    r = sync_src.sync_status(_settings(), env="prod")
    assert r["available"] is False and "argocd_url" in r["reason"]


def test_sync_missing_token():
    r = sync_src.sync_status(_settings(argocd_url="https://argo"), env="prod")
    assert r["available"] is False


def test_sync_success(monkeypatch):
    monkeypatch.setattr("backend.dashboard.sources.sync.get_app_status",
                        lambda *a, **k: SimpleNamespace(success=True, sync_status="Synced",
                                                        health_status="Healthy", revision="abc123"))
    monkeypatch.setattr("cli.utils.config.get_argocd_app_name", lambda config, env: "guardops-app-prod")
    r = sync_src.sync_status(_settings(argocd_url="https://argo", argocd_token="t"), env="prod")
    assert r["available"] is True and r["sync_status"] == "Synced" and r["health_status"] == "Healthy"


def test_sync_unreachable(monkeypatch):
    monkeypatch.setattr("backend.dashboard.sources.sync.get_app_status",
                        lambda *a, **k: SimpleNamespace(success=False, error_message="connection refused"))
    monkeypatch.setattr("cli.utils.config.get_argocd_app_name", lambda config, env: "app")
    r = sync_src.sync_status(_settings(argocd_url="https://argo", argocd_token="t"), env="prod")
    assert r["available"] is False and "connection refused" in r["reason"]


# ── metrics (Prometheus) ──────────────────────────────────────────────────────

def test_app_metrics_unconfigured():
    assert metrics_src.app_metrics(_settings())["available"] is False


def test_app_metrics_success(monkeypatch):
    monkeypatch.setattr("backend.dashboard.sources.metrics._query", lambda *a, **k: [{"metric": {}}])
    r = metrics_src.app_metrics(_settings(prometheus_url="http://prom"))
    assert r["available"] is True


def test_app_metrics_query_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("prometheus down")
    monkeypatch.setattr("backend.dashboard.sources.metrics._query", boom)
    r = metrics_src.app_metrics(_settings(prometheus_url="http://prom"))
    assert r["available"] is False and "prometheus down" in r["reason"]


def test_resource_metrics_success(monkeypatch):
    monkeypatch.setattr("backend.dashboard.sources.metrics._query", lambda *a, **k: [])
    r = metrics_src.resource_metrics(_settings(prometheus_url="http://prom"), namespace="default")
    assert r["available"] is True and r["namespace"] == "default"


# ── quarantine (kubectl) ──────────────────────────────────────────────────────

def test_quarantine_no_kubectl(monkeypatch):
    monkeypatch.setattr("backend.dashboard.sources.quarantine.shutil.which", lambda _: None)
    assert q_src.quarantine_status()["available"] is False


def test_quarantine_success(monkeypatch):
    monkeypatch.setattr("backend.dashboard.sources.quarantine.shutil.which", lambda _: "/usr/bin/kubectl")
    netpols = {"items": [{"metadata": {"name": "np1", "namespace": "default", "creationTimestamp": "t"}}]}
    pods = {"items": [{"metadata": {"name": "p1", "namespace": "default"},
                       "status": {"phase": "Running"}, "spec": {"nodeName": "n1"}}]}

    def fake_json(args, timeout=15):
        return netpols if "networkpolicy" in args else pods

    monkeypatch.setattr("backend.dashboard.sources.quarantine._kubectl_json", fake_json)
    r = q_src.quarantine_status()
    assert r["available"] is True and len(r["policies"]) == 1 and len(r["pods"]) == 1


def test_quarantine_kubectl_error(monkeypatch):
    monkeypatch.setattr("backend.dashboard.sources.quarantine.shutil.which", lambda _: "/usr/bin/kubectl")

    def boom(args, timeout=15):
        raise RuntimeError("no cluster")

    monkeypatch.setattr("backend.dashboard.sources.quarantine._kubectl_json", boom)
    assert q_src.quarantine_status()["available"] is False


# ── runtime (Falco via Loki) ──────────────────────────────────────────────────

def test_runtime_alerts_unconfigured():
    assert rt_src.runtime_alerts(_settings())["available"] is False


def test_runtime_alerts_success(monkeypatch):
    monkeypatch.setattr("backend.dashboard.sources.runtime.query_falco_alerts",
                        lambda *a, **k: SimpleNamespace(success=True, severity_counts={"CRITICAL": 1}, alerts=[]))
    r = rt_src.runtime_alerts(_settings(loki_url="http://loki"))
    assert r["available"] is True and r["counts"] == {"CRITICAL": 1}


def test_runtime_alerts_failure(monkeypatch):
    monkeypatch.setattr("backend.dashboard.sources.runtime.query_falco_alerts",
                        lambda *a, **k: SimpleNamespace(success=False, error_message="loki down", skip_reason=""))
    assert rt_src.runtime_alerts(_settings(loki_url="http://loki"))["available"] is False

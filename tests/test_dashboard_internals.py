"""
tests/test_dashboard_internals.py — dashboard source/settings internals.

The Prometheus _query helper, the quarantine _kubectl_json helper, the findings
compute_summary/compute_diff derived views, and settings env-override resolution.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.dashboard.sources import metrics as metrics_src
from backend.dashboard.sources import quarantine as q_src
from backend.dashboard.sources import findings as findings_src
from backend.dashboard.settings import load_settings


# ── metrics._query / resource_metrics ─────────────────────────────────────────

def test_query_success(monkeypatch):
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json.return_value = {"status": "success", "data": {"result": [{"x": 1}]}}
    monkeypatch.setattr(metrics_src.requests, "get", lambda *a, **k: resp)
    assert metrics_src._query("http://prom", "up") == [{"x": 1}]


def test_query_raises_on_error_status(monkeypatch):
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json.return_value = {"status": "error", "error": "bad query"}
    monkeypatch.setattr(metrics_src.requests, "get", lambda *a, **k: resp)
    with pytest.raises(RuntimeError):
        metrics_src._query("http://prom", "up")


def test_resource_metrics_error_degrades(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("prom down")
    monkeypatch.setattr(metrics_src, "_query", boom)
    r = metrics_src.resource_metrics(SimpleNamespace(prometheus_url="http://p"), namespace="default")
    assert r["available"] is False and "prom down" in r["reason"]


# ── quarantine._kubectl_json ──────────────────────────────────────────────────

def test_kubectl_json_success(monkeypatch):
    monkeypatch.setattr(q_src.subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=0, stdout='{"items":[]}'))
    assert q_src._kubectl_json(["get", "pods"]) == {"items": []}


def test_kubectl_json_failure_raises(monkeypatch):
    monkeypatch.setattr(q_src.subprocess, "run",
                        lambda *a, **k: MagicMock(returncode=1, stdout="", stderr="boom"))
    with pytest.raises(RuntimeError):
        q_src._kubectl_json(["get", "pods"])


# ── findings.compute_summary / compute_diff ───────────────────────────────────

def test_compute_summary_with_runs():
    run = SimpleNamespace(id=1, blocked=False, crit_count=1, high_count=0, medium_count=0,
                          low_count=0, to_dict=lambda: {"id": 1})
    store = SimpleNamespace(
        list_runs=lambda project=None, limit=200: [run],
        query_findings=lambda run_id=None, limit=0: [SimpleNamespace(tool="trivy")],
    )
    out = findings_src.compute_summary(store)
    assert out["total_runs"] == 1 and out["gate_pass_rate"] == 1.0
    assert out["by_tool"][0]["tool"] == "trivy"


def test_compute_diff_no_runs():
    store = SimpleNamespace(list_runs=lambda project=None, limit=1: [])
    assert findings_src.compute_diff(store)["empty"] is True


def test_compute_diff_to_id_missing_raises():
    store = SimpleNamespace(get_run=lambda rid: None)
    with pytest.raises(LookupError):
        findings_src.compute_diff(store, to_id=99)


def test_compute_diff_no_earlier_run():
    to_run = SimpleNamespace(id=2, project_name="p", to_dict=lambda: {"id": 2})
    store = SimpleNamespace(
        list_runs=lambda project=None, limit=1: [to_run],
        latest_run_before=lambda run_id=None, project=None: None,
    )
    out = findings_src.compute_diff(store)
    assert out["empty"] is True and "no earlier run" in out["reason"]


# ── settings env overrides ────────────────────────────────────────────────────

def test_settings_env_overrides(monkeypatch):
    monkeypatch.setenv("GUARDOPS_METADATA_BACKEND", "s3")
    monkeypatch.setenv("GUARDOPS_S3_BUCKET", "mybucket")
    monkeypatch.setenv("GUARDOPS_S3_PREFIX", "pre")
    monkeypatch.setenv("GUARDOPS_AWS_REGION", "us-east-1")
    monkeypatch.setenv("GUARDOPS_DASHBOARD_CORS_ORIGINS", "https://a, https://b")
    s = load_settings({"project": {"name": "x"}})
    assert s.config["metadata"]["backend"] == "s3"
    assert s.config["metadata"]["s3_bucket"] == "mybucket"
    assert "https://a" in s.cors_origins and "https://b" in s.cors_origins


def test_settings_loads_config_file(runner):
    with runner.isolated_filesystem():
        Path(".guardops.yaml").write_text("project:\n  name: fromfile\n", encoding="utf-8")
        s = load_settings()       # no arg → loads the on-disk config
        assert s.project_name == "fromfile"

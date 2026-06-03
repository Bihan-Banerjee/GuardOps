"""
backend/dashboard/snapshot.py

Builds a single static JSON snapshot of the dashboard API so the public SPA can
render last-known data when the live backend (app.guardops.live) is unreachable.

WHY: the EKS cluster is torn down nightly to stay near-zero cost, so the in-cluster
dashboard API only answers while `scripts/morning-start.ps1` has the cluster up.
The Vercel-hosted SPA (dashboard.guardops.live) is always reachable, but its data
isn't — so from a phone or another laptop the site looks dead overnight. A snapshot
published to a public, always-reachable URL fixes that: the SPA falls back to it and
shows a "live backend offline — data from <time>" banner (web/src/api.js).

HOW: build_snapshot() captures every GET route create_app() mounts, keyed by the
exact API path the frontend requests, using the same source functions the routes
call (so the snapshot can never drift from the live shape). Durable routes
(summary/runs/findings/trends) always carry data; live routes (metrics/runtime/
quarantine/sync) carry their last-known {"available": ...} payload, captured while
the cluster is still up — e.g. just before night-shutdown.

The snapshot is a plain dict; `guardops dashboard snapshot` serialises it to a file
and/or uploads it to S3 (reusing backend.metadata.s3_store.put_export_to_s3).
"""

from datetime import datetime, timezone

from cli import __version__
from backend.metadata.factory import get_store
from backend.dashboard.settings import DashboardSettings, load_settings
from backend.dashboard.sources import findings as findings_src
from backend.dashboard.sources import metrics as metrics_src
from backend.dashboard.sources import quarantine as quarantine_src
from backend.dashboard.sources import runtime as runtime_src
from backend.dashboard.sources import sync as sync_src

_EMPTY_SUMMARY: dict = {
    "total_runs": 0,
    "latest": None,
    "by_severity": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
    "gate_pass_rate": None,
    "by_tool": [],
    "recent": [],
}


# Live-source payloads carry raw connection errors and target URLs (the EKS API
# endpoint, in-cluster service DNS). The snapshot is published to a PUBLIC object,
# so those internal details must be stripped — we keep only the rendered data
# (counts/alerts/pods/metrics), not where it came from or why a fetch failed.
_LEAK_KEYS = ("prometheus_url", "loki_url", "url", "host")


def _redact_live(payload: dict) -> dict:
    """Strip internal endpoints + raw error text from a live-source payload."""
    if not isinstance(payload, dict):
        return payload
    p = {k: v for k, v in payload.items() if k not in _LEAK_KEYS}
    if p.get("available") is False:
        # A specific reason (hostnames, stack traces) must not reach a public file.
        p["reason"] = "live source unavailable while the cluster is offline"
        p.pop("_error", None)
    return p


def _meta(settings: DashboardSettings) -> dict:
    """Mirror of the /api/v1/meta route (backend/dashboard/app.py)."""
    s = settings
    return {
        "version": __version__,
        "project": s.project_name,
        "metadata_backend": (s.config.get("metadata", {}) or {}).get("backend", "sqlite"),
        "auth_mode": s.auth_mode,
        "auth_enabled": s.auth_enabled,
        "sources": {
            "prometheus": bool(s.prometheus_url),
            "loki": bool(s.loki_url),
            "argocd": bool(s.argocd_url and s.argocd_token),
        },
    }


def build_snapshot(
    settings: DashboardSettings | None = None,
    *,
    store=None,
    runs_limit: int = 50,
    findings_limit: int = 500,
    trend_days: int = 30,
    runtime_since: str = "24h",
) -> dict:
    """Assemble the full dashboard snapshot.

    Keyed by API path so the frontend fallback is a direct lookup: a failed live
    fetch for `/api/v1/summary` reads snapshot["data"]["/api/v1/summary"]. Never
    raises — any per-route failure degrades that one entry to a sensible default so
    a single flaky source can't void the whole snapshot.
    """
    settings = settings or load_settings()
    store = store if store is not None else get_store(settings.config)

    def safe(fn, default):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - a snapshot must never fail as a whole
            if isinstance(default, dict):
                return {**default, "_error": str(e)}
            return default

    data = {
        "/api/v1/meta": _meta(settings),
        "/api/v1/summary": safe(lambda: findings_src.compute_summary(store), _EMPTY_SUMMARY),
        "/api/v1/runs": safe(
            lambda: {"runs": [r.to_dict() for r in store.list_runs(limit=runs_limit)]},
            {"runs": []},
        ),
        "/api/v1/findings": safe(
            lambda: {"findings": [f.to_dict() for f in store.query_findings(limit=findings_limit)]},
            {"findings": []},
        ),
        "/api/v1/trends": safe(
            lambda: {
                "days": trend_days,
                "points": [p.to_dict() for p in store.severity_trends(days=trend_days)],
            },
            {"days": trend_days, "points": []},
        ),
        "/api/v1/metrics/app": _redact_live(safe(
            lambda: metrics_src.app_metrics(settings),
            {"available": False, "reason": "not captured"},
        )),
        "/api/v1/metrics/resources": _redact_live(safe(
            lambda: metrics_src.resource_metrics(settings),
            {"available": False, "reason": "not captured"},
        )),
        "/api/v1/runtime/alerts": _redact_live(safe(
            lambda: runtime_src.runtime_alerts(settings, since=runtime_since),
            {"available": False, "reason": "not captured"},
        )),
        "/api/v1/quarantine": _redact_live(safe(
            lambda: quarantine_src.quarantine_status(),
            {"available": False, "reason": "not captured"},
        )),
        "/api/v1/sync-status": _redact_live(safe(
            lambda: sync_src.sync_status(settings),
            {"available": False, "reason": "not captured"},
        )),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": __version__,
        "project": settings.project_name,
        "data": data,
    }

"""
backend/dashboard/app.py

The GuardOps dashboard FastAPI application (Phase 13).

`create_app(settings)` builds the app: it constructs a single MetadataStore at
startup (so the S3 backend's TTL cache is shared across requests), mounts the
read-only /api/v1 routes behind the shared-credential auth dependency, and exposes
unauthenticated /healthz + /readyz probes (mirroring the Phase 8 webhook handler).

Run locally with `guardops dashboard`; in-cluster uvicorn targets
`backend.dashboard.app:app`. The frontend SPA consumes this JSON API and is built
separately — every route returns plain JSON.

Response convention:
  - Durable findings routes always return data (empty when the store is empty).
  - Live routes (metrics/runtime/quarantine/sync) return {"available": false,
    "reason": ...} when their source is unreachable, never a 5xx — the cluster is
    torn down nightly and "unavailable" is an expected, renderable state.
"""

from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from cli import __version__
from backend.metadata.factory import get_store
from backend.dashboard.auth import make_auth_dependency
from backend.dashboard.settings import DashboardSettings, load_settings
from backend.dashboard.sources import findings as findings_src
from backend.dashboard.sources import metrics as metrics_src
from backend.dashboard.sources import quarantine as quarantine_src
from backend.dashboard.sources import runtime as runtime_src
from backend.dashboard.sources import sync as sync_src


def create_app(settings: Optional[DashboardSettings] = None) -> FastAPI:
    settings = settings or load_settings()
    store = get_store(settings.config)

    app = FastAPI(title="GuardOps Dashboard API", version=__version__)
    app.state.settings = settings
    app.state.store = store

    # The SPA is cross-origin (e.g. guardops.live -> app.guardops.live), so CORS is
    # required for the browser to read responses and send the Authorization header.
    # Starlette short-circuits the OPTIONS preflight before the auth dependency, so
    # preflight is never blocked by auth.
    if settings.cors_origins or settings.cors_origin_regex:
        cors_kwargs: dict = {
            "allow_origins": settings.cors_origins,
            "allow_methods": ["GET", "OPTIONS"],
            "allow_headers": ["*"],
        }
        if settings.cors_origin_regex:
            cors_kwargs["allow_origin_regex"] = settings.cors_origin_regex
        app.add_middleware(CORSMiddleware, **cors_kwargs)

    _register_health(app)
    _register_api(app, make_auth_dependency(settings))
    return app


# ── Health + root (unauthenticated) ────────────────────────────────────────--

def _register_health(app: FastAPI) -> None:
    @app.get("/")
    def root():
        return {
            "name": "GuardOps Dashboard API",
            "version": __version__,
            "api": "/api/v1",
            "docs": "/docs",
        }

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    def readyz(request: Request):
        settings: DashboardSettings = request.app.state.settings
        backend = (settings.config.get("metadata", {}) or {}).get("backend", "sqlite")
        # Best-effort store probe — never fail readiness on an empty/missing DB.
        store_ok = True
        reason = ""
        try:
            request.app.state.store.list_runs(limit=1)
        except Exception as e:  # noqa: BLE001
            store_ok = False
            reason = str(e)
        return {
            "status": "ready" if store_ok else "degraded",
            "metadata_backend": backend,
            "auth_enabled": settings.auth_enabled,
            "store_reason": reason,
        }


# ── API (authenticated) ─────────────────────────────────────────────────────--

def _register_api(app: FastAPI, auth) -> None:
    dep = [Depends(auth)]
    api = "/api/v1"

    @app.get(api + "/meta", dependencies=dep)
    def meta(request: Request):
        s: DashboardSettings = request.app.state.settings
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

    @app.get(api + "/summary", dependencies=dep)
    def summary(request: Request, project: Optional[str] = None):
        return findings_src.compute_summary(request.app.state.store, project=project)

    @app.get(api + "/runs", dependencies=dep)
    def runs(request: Request, project: Optional[str] = None, env: Optional[str] = None,
             image: Optional[str] = None, limit: int = 50):
        rows = request.app.state.store.list_runs(
            project=project, environment=env, image=image, limit=limit
        )
        return {"runs": [r.to_dict() for r in rows]}

    @app.get(api + "/runs/{run_id}", dependencies=dep)
    def run_detail(request: Request, run_id: int):
        store = request.app.state.store
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run #{run_id} not found")
        findings = store.query_findings(run_id=run_id, limit=1_000_000)
        return {"run": run.to_dict(), "findings": [f.to_dict() for f in findings]}

    @app.get(api + "/findings", dependencies=dep)
    def findings(request: Request, run_id: Optional[int] = None, severity: Optional[str] = None,
                 tool: Optional[str] = None, cve: Optional[str] = None,
                 image: Optional[str] = None, limit: int = 200):
        rows = request.app.state.store.query_findings(
            run_id=run_id, severity=severity, tool=tool, cve=cve, image=image, limit=limit
        )
        return {"findings": [f.to_dict() for f in rows]}

    @app.get(api + "/trends", dependencies=dep)
    def trends(request: Request, project: Optional[str] = None,
               env: Optional[str] = None, days: int = 30):
        points = request.app.state.store.severity_trends(
            project=project, environment=env, days=days
        )
        return {"days": days, "points": [p.to_dict() for p in points]}

    @app.get(api + "/diff", dependencies=dep)
    def diff(request: Request, from_id: Optional[int] = None,
             to_id: Optional[int] = None, project: Optional[str] = None):
        try:
            return findings_src.compute_diff(
                request.app.state.store, from_id=from_id, to_id=to_id, project=project
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get(api + "/metrics/app", dependencies=dep)
    def metrics_app(request: Request):
        return metrics_src.app_metrics(request.app.state.settings)

    @app.get(api + "/metrics/resources", dependencies=dep)
    def metrics_resources(request: Request, namespace: str = "default"):
        return metrics_src.resource_metrics(request.app.state.settings, namespace=namespace)

    @app.get(api + "/runtime/alerts", dependencies=dep)
    def runtime_alerts(request: Request, since: str = "1h", namespace: Optional[str] = None,
                       severity: str = "LOW", limit: int = 200):
        return runtime_src.runtime_alerts(
            request.app.state.settings, since=since, namespace=namespace,
            min_severity=severity, limit=limit,
        )

    @app.get(api + "/quarantine", dependencies=dep)
    def quarantine(request: Request, namespace: Optional[str] = None,
                   all_namespaces: bool = False):
        return quarantine_src.quarantine_status(
            namespace=namespace, all_namespaces=all_namespaces
        )

    @app.get(api + "/sync-status", dependencies=dep)
    def sync_status(request: Request, env: str = "prod"):
        return sync_src.sync_status(request.app.state.settings, env=env)

    @app.get(api + "/export", dependencies=dep)
    def export(request: Request):
        return request.app.state.store.export_json()


# Module-level app for uvicorn (`backend.dashboard.app:app`) and `guardops dashboard`.
app = create_app()

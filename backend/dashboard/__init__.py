"""
backend/dashboard — GuardOps web dashboard backend (Phase 13, v1.0.0).

A FastAPI service that visualizes everything the CLI produces: scan findings,
history, trends and diffs (durable, read from the S3 export bridge), plus live
observability metrics (Prometheus), runtime security alerts (Loki/Falco), pod
quarantine state (kubectl) and ArgoCD sync status.

The HTTP plumbing (app.py / routes.py) is kept separate from the data adapters
(sources/) which simply reuse the existing backend modules — mirroring the
separation in backend/security/alertmanager_handler.py. The frontend SPA is a
separate, later deliverable; this package is a frontend-agnostic JSON API.
"""

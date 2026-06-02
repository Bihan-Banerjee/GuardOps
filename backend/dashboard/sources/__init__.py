"""
backend/dashboard/sources — data adapters for the dashboard API.

Each adapter is a thin wrapper over an existing backend module so the dashboard
adds no new data logic:
  findings.py  → backend.metadata.* (durable scan data via the configured store)
  metrics.py   → Prometheus HTTP API (observability)
  runtime.py   → backend.security.falco_reader (Loki/Falco alerts)
  quarantine.py→ kubectl (self-healing state)
  sync.py      → backend.pipeline.gitops_writer (ArgoCD)

Live adapters (metrics/runtime/quarantine/sync) return {"available": False,
"reason": ...} instead of raising when their source is unreachable — the cluster
is torn down nightly, so "unavailable" is a normal state the dashboard renders as
a card rather than an error.
"""

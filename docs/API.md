# Dashboard API

The dashboard backend (`backend/dashboard/app.py`, FastAPI) serves a read-only JSON
API the SPA consumes. Run it locally with `guardops dashboard` (default
`http://0.0.0.0:8081`); interactive docs are at `/docs`.

## Auth

Routes under `/api/v1` require a credential **when one is configured** (so local dev is
frictionless). Modes (`GUARDOPS_DASHBOARD_AUTH_MODE`):

- `token` — `Authorization: Bearer <GUARDOPS_DASHBOARD_TOKEN>`
- `basic` — `Authorization: Basic <base64(user:password)>` (`GUARDOPS_DASHBOARD_USER` / `_PASSWORD`)
- `none` — no auth (local only)

Health probes (`/healthz`, `/readyz`) and `/` are always unauthenticated. The SPA is
cross-origin, so CORS is restricted to the configured origins
(`GUARDOPS_DASHBOARD_CORS_ORIGINS`).

## Routes

| Method | Path | Returns |
|--------|------|---------|
| GET | `/healthz` | `{status, version}` |
| GET | `/readyz` | readiness + metadata-store probe |
| GET | `/api/v1/meta` | version, project, backend, auth mode, which live sources are configured |
| GET | `/api/v1/summary` | landing cards: latest run, severity totals, gate pass-rate, per-tool counts |
| GET | `/api/v1/runs?limit=&project=&env=&image=` | recent scan runs |
| GET | `/api/v1/runs/{id}` | one run + its findings |
| GET | `/api/v1/findings?severity=&tool=&cve=&run_id=&limit=` | filtered findings (severity = "at or above") |
| GET | `/api/v1/trends?days=&project=&env=` | per-day severity rollup |
| GET | `/api/v1/diff?from_id=&to_id=` | new-vs-fixed findings between two runs |
| GET | `/api/v1/metrics/app` | guardops_* Prometheus metrics (live) |
| GET | `/api/v1/metrics/resources?namespace=` | pod CPU/memory (live) |
| GET | `/api/v1/runtime/alerts?since=&severity=` | Falco alerts via Loki (live) |
| GET | `/api/v1/quarantine?namespace=&all_namespaces=` | quarantined pods + policies (live) |
| GET | `/api/v1/sync-status?env=` | ArgoCD sync + health (live) |
| GET | `/api/v1/export` | the full durable store as JSON |

**Durable vs live:** durable routes (summary/runs/findings/trends) always return data
(empty when the store is empty). Live routes return `{"available": false, "reason": …}`
— never a 5xx — when their source (Prometheus/Loki/kubectl/ArgoCD) is unreachable,
because the cluster is torn down nightly and "unavailable" is an expected state.

## Static snapshot

For the public SPA, `guardops dashboard snapshot` serialises every route above into one
JSON document keyed by path (`{generated_at, version, project, data: {"/api/v1/…": …}}`).
Live-source payloads are **redacted** (internal endpoints/hostnames stripped) before the
snapshot is published, since it is served from a public URL. The SPA falls back to it
when the live API is down. See [backend/dashboard/snapshot.py](../backend/dashboard/snapshot.py).

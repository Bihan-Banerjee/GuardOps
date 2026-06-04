# Self-hosting the GuardOps dashboard

GuardOps is a bring-your-own-infrastructure CLI — **you don't need to host anything**
for others to `pip install guardops` and use it. This guide is for when you want a
**live dashboard that's always reachable without running a full EKS cluster** — for a
personal demo, a team, or a portfolio site — at near-zero cost.

## What you get (and what you don't)

The dashboard backend serves two kinds of data:

- **Durable** — findings / runs / trends / summary / diff. Read from object storage
  (an S3 export), so these work **anywhere**, with no cluster.
- **Live** — metrics / runtime / quarantine / sync. These query in-cluster
  Prometheus / Loki / kubectl / ArgoCD, so when the backend runs **off-cluster** they
  return `{"available": false}` and the panels show "unavailable" — the same graceful
  degradation as the offline snapshot. The findings dashboard still works fully.

So self-hosting gives you the **findings dashboard** 24/7 without a cluster.

## Pick an approach (cheapest first)

| Approach | Always-on? | Cost | Best for |
|---|---|---|---|
| **1. Static snapshot only** (no backend) | ✅ | $0 | A public showcase — the SPA + a public S3 snapshot (the built-in offline fallback) |
| **2. Local backend** | ❌ (your machine) | $0 | Personal use / development |
| **3. Hosted backend** (small container) | ✅ | ~$0–5/mo | A team or always-on demo with no cluster |

Approach 1 is already covered by the snapshot fallback — see the README's
"Cluster lifecycle & the always-on dashboard". This guide focuses on **2** and **3**.

## Option 2 — local backend

```bash
pip install 'guardops[dashboard]'
guardops dashboard            # http://localhost:8081  (API + /docs)
```
It reads your local SQLite metadata DB by default, or an S3 export if you set
`metadata.backend: s3` (see below). Point a local SPA at it with `VITE_API_URL=http://localhost:8081`.

## Option 3 — hosted backend (Fly.io / Render / Cloud Run / VPS)

The backend is a plain FastAPI app (`backend.dashboard.app:app`) with built-in auth —
run it on any container host. Use this **portable Dockerfile** (it builds from a public
base and picks up the correct dependency pins, unlike the in-cluster `Dockerfile.dashboard`
which layers onto your private ECR image):

```dockerfile
# Dockerfile.dashboard.portable
FROM python:3.11-slim
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "guardops[dashboard]"
EXPOSE 8081
CMD ["python", "-m", "uvicorn", "backend.dashboard.app:app", \
     "--host", "0.0.0.0", "--port", "8081"]
```

### 1. Publish your data to storage

```bash
guardops db export --to-s3 --bucket <bucket>            # durable findings
guardops dashboard snapshot --to-s3 --bucket <bucket>   # static offline fallback
```
> Storage today is **AWS S3** (cheap — a few cents for tiny JSON). S3-compatible
> endpoints (Cloudflare R2 with free egress, MinIO, DO Spaces) are a planned
> `metadata.s3_endpoint_url` option — track it in the roadmap.

### 2. Configure via environment variables

The pod has no `.guardops.yaml`, so everything is env-driven:

| Variable | Purpose |
|---|---|
| `GUARDOPS_METADATA_BACKEND=s3` | Read durable data from the S3 export |
| `GUARDOPS_S3_BUCKET`, `GUARDOPS_S3_PREFIX`, `GUARDOPS_AWS_REGION` | Where the export lives |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (or an instance role) | Read access to the bucket |
| `GUARDOPS_DASHBOARD_TOKEN` *or* `GUARDOPS_DASHBOARD_USER` + `_PASSWORD` | **Auth — always set one for a public deployment** |
| `GUARDOPS_DASHBOARD_AUTH_MODE=token\|basic` | Which auth scheme |
| `GUARDOPS_DASHBOARD_CORS_ORIGINS` | Comma-separated SPA origins allowed to read the API |

### 3. Deploy

```bash
# Fly.io
fly launch --dockerfile Dockerfile.dashboard.portable --internal-port 8081
fly secrets set GUARDOPS_METADATA_BACKEND=s3 GUARDOPS_S3_BUCKET=<bucket> \
  GUARDOPS_AWS_REGION=<region> AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
  GUARDOPS_DASHBOARD_TOKEN=<token> GUARDOPS_DASHBOARD_CORS_ORIGINS=https://your-spa
fly deploy
```
Render: *New → Web Service*, Docker, port `8081`, set the same env vars.
Cloud Run: `gcloud run deploy --port 8081 --source .`.
VPS: `docker run -p 8081:8081 --env-file dashboard.env <image>` behind a TLS reverse proxy.

### 4. Point the SPA at it

In the SPA build (`web/.env` or your Vercel project):
```
VITE_API_URL=https://your-backend.fly.dev
VITE_API_TOKEN=<token>          # matches GUARDOPS_DASHBOARD_TOKEN
VITE_SNAPSHOT_URL=https://.../dashboard/snapshot.json   # offline fallback
```

### 5. Keep it fresh

Schedule the two publish commands (cron, a CI job, or your `morning-start.ps1`) so the
hosted dashboard reflects new scans:
```bash
guardops db export --to-s3 --bucket <bucket>
guardops dashboard snapshot --to-s3 --bucket <bucket>
```

## Cost (Option 3)

- **Fly.io** `shared-cpu-1x` (256 MB): free tier → ~$2/mo.
- **S3** storage for tiny JSON exports: a few cents.
- **Total: ~$0–3/month**, always-on, no cluster.

## Security checklist

- [ ] **Always set an auth credential** (`GUARDOPS_DASHBOARD_TOKEN` or basic) for any
      public deployment — without one, auth is disabled.
- [ ] Restrict `GUARDOPS_DASHBOARD_CORS_ORIGINS` to your SPA's origin.
- [ ] Use a read-only IAM identity for the bucket.
- [ ] Remember the dashboard exposes **your** scan findings (a map of your weaknesses) —
      only publish data you're comfortable being visible to whoever can reach the URL.

## Related

- [README](../README.md) — the cluster-lifecycle + snapshot model
- [docs/API.md](API.md) — the routes the SPA consumes
- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — how the pieces fit

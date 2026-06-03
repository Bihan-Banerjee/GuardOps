# Architecture

GuardOps is a monorepo with four parts: a Python CLI, a FastAPI dashboard backend, a
React SPA, and the Terraform/Kubernetes infrastructure they run on.

```
cli/                 Click CLI — `guardops <command>`
  main.py            root group; registers every subcommand
  commands/          one file per command (deploy, scan, status, dashboard, doctor, …)
  utils/             config (.guardops.yaml), output (Rich), system (subprocess wrappers)

backend/             non-CLI libraries the commands call
  pipeline/          builder (Docker) · pusher (ECR) · deployer (Helm) · gitops_writer (ArgoCD)
  security/          semgrep · bandit · trivy · zap · falco_reader · cosign · syft · report_generator
  metadata/          MetadataStore: sqlite_store (local/CI) · s3_store (dashboard) · factory
  dashboard/         FastAPI app · auth · settings · sources/* (findings, metrics, runtime, …) · snapshot

web/                 Vite + React + Three.js SPA (dashboard.guardops.live)
  src/api.js         live→snapshot fallback client
  src/components/    Hero, Trends, Runs, Findings, Runtime, Metrics, OfflineBanner, …

infra/terraform/     AWS IaC: vpc · eks · ecr · s3 · iam · route53/dns-tls · argocd · kyverno · falco
k8s/                 Helm chart + ArgoCD apps + Falco/Kyverno/observability manifests
scripts/             PowerShell lifecycle: morning-start · night-shutdown · setup-*
tests/               pytest suite + the bundled demo (test_project/)
```

## The deploy pipeline

`guardops deploy` (`cli/commands/deploy_cmd.py`) orchestrates:

```
build (builder) → scan (security/*) → [persist to MetadataStore]
   → push (pusher, staging/prod) + cosign sign + SBOM
   → deploy (deployer: Helm) and/or GitOps override (gitops_writer → ArgoCD)
   → DAST (zap_runner, prod) — auto-rollback on CRITICAL
```

HIGH+ findings block at the SAST gate; CRITICAL DAST triggers rollback. Runtime Falco
alerts (`runtime-status`) and ArgoCD sync (`sync-status`) act as post-deploy CI gates.

## Data flow for the dashboard

```
CLI/CI scans → MetadataStore (SQLite) → `db export --to-s3` → S3 object
                                                              ↓
in-cluster dashboard pod (metadata.backend=s3) reads it ──→ /api/v1 (app.guardops.live)
                                                              ↓
SPA (dashboard.guardops.live) ── live ──→ renders
                              └─ offline ─→ static snapshot (dashboard/snapshot.json, public)
```

The cluster is ephemeral (created/destroyed daily). The S3 export and the static
snapshot survive teardown, so durable findings persist with **no always-on database**.

## Key design rules

- Commands depend on `MetadataStore`, never `sqlite3` — swapping backends is a factory
  line (`backend/metadata/factory.py`).
- Persistence and live-source failures are **non-fatal**: a DB hiccup or a down cluster
  degrades to a warning or `{"available": false}`, never a crash.
- All terminal output goes through `cli/utils/output.py`; all subprocess calls through
  `cli/utils/system.py`.

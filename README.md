# GuardOps

> Production-grade DevSecOps CLI. Build, scan, deploy, monitor runtime security, and self-heal with gates at every stage.

[![PyPI version](https://img.shields.io/pypi/v/guardops.svg)](https://pypi.org/project/guardops/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/Bihan-Banerjee/GuardOps/actions/workflows/ci.yaml/badge.svg)](https://github.com/Bihan-Banerjee/GuardOps/actions)

GuardOps wraps a complete secure delivery pipeline behind a single command. Given any application repo, it builds a Docker image, runs four security scanners in sequence, deploys to Kubernetes via Helm, runs a post-deploy DAST scan, exposes live metrics to Prometheus, queries runtime security alerts from Loki, and automatically quarantines compromised pods via Alertmanager webhooks — blocking the pipeline if HIGH or CRITICAL findings are detected at any stage.

```
guardops deploy --env prod
```

That one command: builds a multi-stage Docker image, runs Semgrep + Bandit + Trivy + SonarQube, pushes to ECR, deploys to EKS via Helm with automatic rollback on failure, runs OWASP ZAP DAST against the live application with auto-rollback on CRITICAL findings, and exposes `/metrics` to a live Grafana dashboard.

```
guardops deploy --env staging --slot blue
guardops switch --slot green --env staging
```

Phase 9 adds multi-environment support and blue-green deploys. Two Helm releases coexist in the `staging` namespace (`guardops-app-staging-blue`, `guardops-app-staging-green`). `guardops switch` patches a shared traffic Service selector to cut traffic between slots in under a second — no image rebuild, no Helm upgrade.

```
guardops runtime-status
```

Queries Loki for Falco-format runtime security alerts (shell spawns, sensitive file reads, package manager execution, /etc writes) and renders a severity-sorted table. Use `--fail-on CRITICAL` as a post-deploy CI gate.

```
guardops quarantine-status --env staging
```

Shows pods currently isolated by the Phase 8 self-healing system — active NetworkPolicies, quarantined pod names, triggering Falco rule, and age. The `--env` flag scopes output to the correct namespace automatically. Use `--release <pod>` to manually lift a quarantine after investigation.

---

## Current Status — v0.9.0

| Phase | Version | Status | What was built |
|-------|---------|--------|----------------|
| 1 — CLI + Local Deploy | v0.1.0 | ✅ Done | Click CLI framework, k3d local deploy, Docker build |
| 2 — Security Scanning + CI | v0.2.0 | ✅ Done | Semgrep, Bandit, Trivy, SonarQube, 154 tests, GitHub Actions |
| 3 — Helm + EKS Infrastructure | v0.3.0 | ✅ Done | Helm deploy, rollback command, Terraform VPC/EKS/IAM/ECR/S3 |
| 4 — Full AWS Pipeline | v0.4.0 | ✅ Done | ECR push, EKS deploy, S3 report upload, cost-optimised infra |
| 4.5 — Reliability Hardening | v0.4.1 | ✅ Done | Remote Terraform state (S3+DynamoDB), multi-stage Docker build, subprocess timeout+encoding fixes, Trivy DB cache in CI |
| 5 — Observability | v0.5.0 | ✅ Done | Prometheus + Grafana via kube-prometheus-stack, `/metrics` endpoint, ServiceMonitor, EBS CSI driver, custom dashboards |
| 6 — Security Hardening | v0.6.1 | ✅ Done | GitHub OIDC replaces IAM user (no more static keys), DAST via OWASP ZAP with post-deploy scan and auto-rollback on CRITICAL |
| 7 — Runtime Security | v0.7.0 | ✅ Done | Loki + Promtail log pipeline, Falco-format alert ingestion, `guardops runtime-status`, CI runtime gate |
| 8 — Self-Healing | v0.8.0 | ✅ Done | Alertmanager webhook handler, automatic NetworkPolicy quarantine on CRITICAL Falco alert, `guardops quarantine-status`, Terraform alertmanager-webhook module |
| 9 — Multi-Environment | v0.9.0 | ✅ Done | Staging + prod namespace separation, blue-green deploy strategy, `guardops switch --slot`, environment-scoped config helpers, `staging-<sha>` ECR tags, `--env` on quarantine-status |

---

## Roadmap

| Phase | Target | What it adds |
|-------|--------|-------------|
| 10 — Full Production | v1.0.0 | Real domain + TLS via cert-manager, ArgoCD GitOps, runbook documentation |

---

## Architecture

```
Developer
    |
    v
guardops deploy [--env local|staging|prod] [--slot blue|green]
    |
    +-- Step 1: Docker Build ──────────────────────────+
    |       Multi-stage build (builder + runtime)       |
    |       pip/wheel absent from final image           |
    |       Non-root user (UID 10001), no shell         |
    |                                                   |
    +-- Step 2: Security Scans (SAST) ─────────────────+
    |       Semgrep    (SAST, code patterns)            |
    |       Bandit     (Python-specific vulns)          |
    |       Trivy fs   (secrets, IaC misconfigs)        |
    |       Trivy img  (CVEs in OS + deps)              |
    |       SonarQube  (quality gate, optional)         |
    |                                                   |
    |       BLOCKED if any finding >= HIGH              |
    |       Report written to security/reports/         |
    |                                                   |
    +-- Step 3: Registry Push ─────────────────────────+
    |       local:   k3d image import                   |
    |       staging: docker push -> ECR (staging-<sha>) |
    |       prod:    docker push -> ECR (<sha>)         |
    |                                                   |
    +-- Step 4: Helm Deploy ───────────────────────────+
    |       helm upgrade --install --atomic             |
    |       local:   k3d + values.yaml                  |
    |       staging: EKS staging ns + values-staging.yaml|
    |       prod:    EKS default ns + values-prod.yaml  |
    |       --slot blue/green: adds guardops.io/slot    |
    |         label to pods + slot-specific release name|
    |       Automatic rollback on timeout or error      |
    |                                                   |
    +-- Step 5: DAST (Phase 6) ────────────────────────+
            OWASP ZAP baseline scan (passive)
            Target: live deployed application
            BLOCKED + auto-rollback if CRITICAL found
            Report written to security/reports/
            Skipped for local + staging (no stable URL)

guardops switch --slot green --env staging (Phase 9)
    |
    +-- Checks readiness of both blue and green pods
    +-- Patches shared traffic Service selector to slot=green
    +-- Verifies endpoint IPs match target slot pods
    +-- Instant cutover — no deploy, no rollout wait

guardops runtime-status (Phase 7)
    |
    +-- Queries Loki HTTP API ({app="falco"} | json)
    +-- Normalises Falco priority -> CRITICAL/HIGH/MEDIUM/LOW
    +-- Renders severity-sorted Rich table in terminal
    +-- --fail-on CRITICAL exits 1 for CI gate use

guardops quarantine-status [--env staging|prod] (Phase 8/9)
    |
    +-- kubectl get networkpolicy -l guardops.io/managed-by=guardops
    +-- kubectl get pods -l guardops.io/quarantine=true
    +-- --env resolves correct namespace automatically
    +-- Renders locked-pod table + active NetworkPolicy table
    +-- --release <pod> lifts quarantine manually
```

### Blue-Green Deploy (Phase 9)

```
guardops deploy --env staging --slot blue
    |
    v
Helm release: guardops-app-staging-blue (namespace: staging)
Pods labelled: guardops.io/slot=blue, app.kubernetes.io/name=guardops-app
    |
guardops deploy --env staging --slot green
    |
    v
Helm release: guardops-app-staging-green (namespace: staging)
Pods labelled: guardops.io/slot=green, app.kubernetes.io/name=guardops-app
    |
Both slots Running simultaneously — no traffic yet
    |
guardops switch --slot blue --env staging
    |
    v
Shared traffic Service "guardops-app" (namespace: staging)
    selector: app.kubernetes.io/name=guardops-app + guardops.io/slot=blue
    annotation: guardops.io/active-slot=blue
    |
All traffic -> blue pods
    |
guardops switch --slot green --env staging
    |
    v
Service selector patched: guardops.io/slot=green
Instant cutover — endpoints change in < 2s (iptables propagation)
    |
Roll back at any time:
guardops switch --slot blue --env staging
```

### Self-Healing Pipeline (Phase 8)

```
CRITICAL Falco alert fires (shell spawn, etc.)
    |
    v
Alertmanager receives alert from Prometheus
    |
    v
POST /webhook -> guardops-alertmanager-webhook pod
    |
    v
alertmanager_handler.py
    +-- kubectl label pod <pod> guardops.io/quarantine=true
    +-- kubectl apply NetworkPolicy (deny-all ingress, DNS-only egress)
    |
    v
Pod isolated — no inbound traffic, no outbound except port 53
    |
    v
Alert resolves (Falco stops firing)
    |
    v
POST /webhook status=resolved
    +-- kubectl delete networkpolicy guardops-quarantine-<fp8>
    +-- kubectl label pod <pod> guardops.io/quarantine-
    |
    v
Pod released — network restored
```

### Infrastructure (AWS, Terraform-managed)

```
ap-south-1 (Mumbai)
+------------------------------------------------------------------+
|  VPC  10.0.0.0/16                                                |
|                                                                  |
|  Public Subnets (ap-south-1a, ap-south-1b)                      |
|    NAT Gateways, Load Balancers                                  |
|                                                                  |
|  Private Subnets (ap-south-1a, ap-south-1b)                     |
|    EKS Managed Node Group (t3.large)                             |
|    +-- default namespace                                         |
|    |    +-- guardops-app Pods (prod, x2)                        |
|    |    +-- /healthz, /ready, /metrics endpoints                 |
|    |    +-- port 8080, non-root UID 10001                        |
|    |    +-- capabilities.drop ALL                                |
|    |                                                             |
|    +-- staging namespace                          [Phase 9]      |
|    |    +-- guardops-app-staging Pods (x1)                      |
|    |    +-- guardops-app-staging-blue Pods        [Phase 9]      |
|    |    +-- guardops-app-staging-green Pods       [Phase 9]      |
|    |    +-- guardops-app Service (traffic switch) [Phase 9]      |
|    |                                                             |
|    +-- monitoring namespace                                      |
|         +-- Prometheus  (kube-prometheus-stack)                  |
|         +-- Grafana     (pre-loaded dashboards)                  |
|         +-- Alertmanager                                         |
|         +-- kube-state-metrics, node-exporter                    |
|         +-- Loki        (log aggregation, 10Gi EBS)  [Phase 7]  |
|         +-- Promtail    (log shipping DaemonSet)      [Phase 7]  |
|         +-- falco-simulator CronJob (every 3 min)    [Phase 7]  |
|         +-- guardops-alertmanager-webhook pod         [Phase 8]  |
|              +-- /healthz, /readyz, /webhook endpoints           |
|              +-- ServiceAccount + ClusterRole (RBAC)             |
|                                                                  |
|  ECR: guardops-app (scan-on-push, 10-image lifecycle)            |
|       :staging-<sha>  (staging builds)               [Phase 9]  |
|       :<sha>          (prod builds)                              |
|       :webhook-latest (handler image)                [Phase 8]  |
|  S3:  guardops-reports-* (scan reports, versioned)               |
|  S3:  guardops-tfstate-* (Terraform remote state)               |
|  DynamoDB: guardops-tf-lock (state locking)                      |
|  IAM: github-actions-role (OIDC, no static keys)                 |
+------------------------------------------------------------------+
```

### CI/CD Pipeline (GitHub Actions)

```
Push to main
    |
    v
Job 1: build-test
    pytest (240+ tests) + ruff + mypy
    |
    v
Job 2: sast
    Semgrep + Bandit — gates on HIGH+
    |
    v
Job 3: container-scan
    Docker build (multi-stage) + Trivy (cached DB)
    Gates on fixable HIGH/CRITICAL CVEs
    ECR push via GitHub OIDC (no static IAM keys)
    |
    v
Job 4: deploy              <-- active when HAS_EKS_CLUSTER=true
    helm upgrade --install --atomic --timeout 5m
    kubectl rollout status verify
    OWASP ZAP DAST scan (passive baseline)  <-- Phase 6
    Auto-rollback on CRITICAL DAST findings <-- Phase 6
    |
    v
Job 5: runtime-gate        <-- active when HAS_FALCO_ENABLED=true [Phase 7]
    kubectl port-forward svc/loki 3100:3100
    guardops runtime-status --since 30m --fail-on CRITICAL
    Exits 1 and fails pipeline if CRITICAL alerts found
    |
    v
Job 6: upload-reports      <-- always runs
    Scan artifacts -> S3 bucket
    Path: reports/<repo>/<branch>/<sha>/<run-id>/
```

---

## Install

```bash
pip install guardops
```

**Requirements:**
- Python 3.11+
- Docker Desktop
- kubectl
- Helm 3.x
- k3d (local deploys) or AWS credentials (prod deploys)

---

## Quick Start

```bash
# Scaffold config in your project directory
guardops init

# Build, scan, and deploy to local k3d
guardops deploy

# Build, scan, push to ECR, deploy to EKS (prod)
guardops deploy --env prod

# Deploy to staging namespace (tag: staging-, ZAP skipped)
guardops deploy --env staging

# Skip SonarQube if not configured
guardops deploy --env prod --skip-sonarqube

# Skip DAST scan (dev only)
guardops deploy --env prod --skip-dast

# Blue-green: deploy both slots into staging
guardops deploy --env staging --slot blue
guardops deploy --env staging --slot green

# Cut traffic to green (instant Service selector patch)
guardops switch --slot green --env staging

# Roll back to blue (no rebuild needed)
guardops switch --slot blue --env staging

# Preview what switch would do without applying
guardops switch --slot green --env staging --dry-run

# View running pod health
guardops status

# Stream pod logs
guardops logs

# Run security scans only (no deploy)
guardops scan

# Roll back to previous Helm revision
guardops rollback

# Roll back to a specific revision
guardops rollback --revision 2

# Check runtime security alerts (Phase 7)
guardops runtime-status

# Filter by time window and severity
guardops runtime-status --since 24h --severity HIGH

# Use as a CI gate (exits 1 if CRITICAL alerts found)
guardops runtime-status --fail-on CRITICAL

# Check quarantine status — Phase 8/9
guardops quarantine-status                      # default namespace
guardops quarantine-status --env staging        # staging namespace
guardops quarantine-status -A                   # all namespaces

# Release a quarantined pod after investigation
guardops quarantine-status --release  --namespace staging
```

---

## Observability

Phase 5 adds a full metrics pipeline from application code to Grafana dashboard.

### Application metrics (`/metrics`)

The test app exposes three custom Prometheus metrics:

| Metric | Type | Description |
|--------|------|-------------|
| `guardops_requests_total` | Counter | Total HTTP requests, labelled by `path` and `status_code` |
| `guardops_request_duration_ms` | Gauge | Last request duration per path in milliseconds |
| `guardops_app_info` | Info | Static build metadata (`environment`, `version`) |

### Viewing metrics

```bash
# Port-forward Grafana
kubectl port-forward svc/kube-prometheus-stack-grafana 3000:80 -n monitoring
# Open http://localhost:3000  (admin / guardops-grafana-2024)

# Port-forward Prometheus
kubectl port-forward svc/kube-prometheus-stack-prometheus 9090:9090 -n monitoring
# Open http://localhost:9090/targets — look for serviceMonitor/default/test-app-guardops-app

# Port-forward Loki (Phase 7)
kubectl port-forward svc/loki 3100:3100 -n monitoring
# Then: guardops runtime-status

# Port-forward Alertmanager (Phase 8)
kubectl port-forward svc/kube-prometheus-stack-alertmanager 9093:9093 -n monitoring
# Open http://localhost:9093 — verify guardops-webhook receiver

# Port-forward webhook handler (Phase 8)
kubectl port-forward svc/guardops-alertmanager-webhook 9095:9095 -n monitoring
# curl http://localhost:9095/healthz  -> {"status":"ok","version":"0.9.0"}
# curl http://localhost:9095/readyz   -> {"status":"ready","kubectl":"..."}
```

### Useful PromQL queries

```promql
# Request rate per path (last 5 minutes)
rate(guardops_requests_total[5m])

# Last request latency per path
guardops_request_duration_ms

# App build metadata
guardops_app_info

# Pod memory usage
container_memory_usage_bytes{namespace="default"}

# CPU usage rate
rate(container_cpu_usage_seconds_total{namespace="default"}[5m])
```

### Useful LogQL queries (Loki, Phase 7)

```logql
# All Falco alerts
{app="falco"} | json

# CRITICAL only (shell spawns)
{app="falco"} | json | priority="Critical"

# Specific rule
{app="falco"} | json | rule="GuardOps Shell Spawned Inside Container"

# Filter by namespace
{app="falco"} | json | line_format "{{.output}}" | k8s_ns_name="default"
```

### Setup (morning start)

```powershell
# After terraform apply and kubectl configure:
.\scripts\setup-observability.ps1           # run from repo root
.\scripts\setup-runtime-security.ps1        # Phase 7: Loki + Promtail + Falco simulator
# Phase 8 webhook handler deployed automatically by terraform apply
# (enable_self_healing = true in terraform.tfvars)
kubectl apply -f k8s/alertmanager/quarantine-webhook.yaml  # Phase 8: wire Alertmanager
```

### Shutdown (nightly — prevents orphaned EBS volumes)

```powershell
helm uninstall kube-prometheus-stack -n monitoring
helm uninstall loki -n monitoring
helm uninstall promtail -n monitoring
kubectl delete pvc --all -n monitoring
Start-Sleep -Seconds 30
cd infra/terraform && terraform destroy -auto-approve
```

---

## Runtime Security (Phase 7)

Phase 7 adds a runtime alert pipeline: structured security events are ingested into Loki and surfaced via `guardops runtime-status`.

### How it works

```
Falco Simulator CronJob (every 3 min)
    └── prints Falco-format JSON to stdout
         └── Promtail DaemonSet tails /var/log/pods/
              └── labels with {app="falco"}, pushes to Loki
                   └── guardops runtime-status queries Loki HTTP API
                        └── FalcoQueryResult -> Rich severity table
                             └── --fail-on CRITICAL -> exit 1 for CI gate
```

### GuardOps Falco Rules (`k8s/falco/custom-rules.yaml`)

| Rule | Falco Priority | GuardOps Severity |
|------|---------------|------------------|
| Shell Spawned Inside Container | CRITICAL | CRITICAL |
| Package Manager Executed in Container | ERROR | HIGH |
| Sensitive File Read in Container | ERROR | HIGH |
| Write to /etc Inside Container | WARNING | MEDIUM |
| Container Running as Root | WARNING | MEDIUM |

### Falco Simulator

The Falco eBPF kernel sensor requires kernel-level perf buffer allocation (`mmap`) that is unavailable in this environment. A Kubernetes CronJob (`k8s/falco/falco-simulator-cronjob.yaml`) fires every 3 minutes and emits identical Falco-format JSON to stdout. Promtail ships these logs to Loki with the `{app="falco"}` label — the entire downstream pipeline (Loki ingestion, `runtime-status` queries, Grafana Explore, CI gate) is functionally identical to real Falco output. Production Falco deployment is fully documented in `infra/terraform/modules/falco/` and `k8s/falco/custom-rules.yaml`.

```powershell
# Deploy the simulator
kubectl apply -f k8s/falco/falco-simulator-configmap.yaml
kubectl apply -f k8s/falco/falco-simulator-cronjob.yaml

# Trigger an alert immediately (without waiting for 3-min cron)
kubectl create job falco-test-1 --from=cronjob/falco-simulator -n monitoring

# Wait for Promtail flush (~90s), then query
guardops runtime-status --since 15m
```

### `guardops runtime-status` output

```
GuardOps Runtime Status — last 15m
  Querying Loki at http://localhost:3100 ...
  Falco alerts (15m) — CRITICAL:1  HIGH:5  MEDIUM:1  LOW:0  (query: 0.1s)

SEV        RULE                                        POD              NAMESPACE  TIME (UTC)
CRITICAL   GuardOps Shell Spawned Inside Container     test-app-...     default    09:27:00
HIGH       GuardOps Sensitive File Read in Container   test-app-...     default    09:27:10
HIGH       GuardOps Package Manager Executed in Con..  test-app-...     default    09:24:01
MEDIUM     GuardOps Write to /etc Inside Container     test-app-...     default    09:15:00

  Top alert (GuardOps Shell Spawned Inside Container):
  Shell spawned inside container (user=root shell=sh proc.cmdline=sh -c id ...)

  Grafana: Explore -> Loki datasource -> {app="falco"} | json
```

---

## Self-Healing (Phase 8)

Phase 8 adds automated incident response: when a CRITICAL Falco alert fires, a FastAPI webhook handler automatically isolates the offending pod using a Kubernetes NetworkPolicy and labels it for visibility. When the alert resolves, the quarantine is automatically lifted.

### How it works

```
CRITICAL Falco alert -> Prometheus -> Alertmanager
    -> POST /webhook -> guardops-alertmanager-webhook pod (FastAPI)
         -> kubectl label pod guardops.io/quarantine=true
         -> kubectl apply NetworkPolicy (deny-all ingress, DNS-only egress)
    Alert resolves -> POST /webhook status=resolved
         -> kubectl delete networkpolicy
         -> kubectl label pod guardops.io/quarantine-
```

### NetworkPolicy applied on quarantine

The handler applies a policy named `guardops-quarantine-<fingerprint[:8]>` that:
- **Ingress:** denies all inbound traffic (no service can reach the pod)
- **Egress:** allows only DNS (UDP/TCP port 53) — keeps logging agents working while blocking all exfiltration vectors (HTTP, HTTPS, raw sockets)

The pod-level quarantine label (`guardops.io/quarantine=true`) ensures only the offending pod is isolated — other replicas of the same deployment continue serving traffic normally.

### Webhook handler

The handler runs as a Kubernetes Deployment in the `monitoring` namespace, deployed by Terraform (`modules/alertmanager-webhook`). It exposes:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/healthz` | GET | Liveness probe — returns `{"status":"ok","version":"0.9.0"}` |
| `/readyz` | GET | Readiness probe — verifies kubectl is reachable; returns 503 if not |
| `/webhook` | POST | Alertmanager webhook receiver |

RBAC: the handler's ServiceAccount is bound to a ClusterRole with the minimum permissions needed — `pods/patch`, `networkpolicies` CRUD, `nodes/patch`.

### `guardops quarantine-status` output

```
GuardOps · Quarantine Status
Phase 8 — Self-Healing  |  Active quarantine policies and isolated pods

Checking namespace(s): default

── Quarantined Pods  (1) ───────────────────────────────────────────────────

╭──────────────────────────────────┬───────────┬──────────┬──────────────────────┬─────╮
│ Pod Name                         │ Namespace │  Phase   │ Node                 │ Age │
├──────────────────────────────────┼───────────┼──────────┼──────────────────────┼─────┤
│ 🔒 guardops-app-d9f557c78-2hxcw  │ default   │ Running  │ ip-10-0-11-46...     │  2m │
╰──────────────────────────────────┴───────────┴──────────┴──────────────────────┴─────╯

── Active Quarantine NetworkPolicies  (1) ──────────────────────────────────

╭────────────────────────────────────────┬───────────┬───────────────────────────────────┬──────────┬─────╮
│ Policy Name                            │ Namespace │ Falco Rule                        │ FP       │ Age │
├────────────────────────────────────────┼───────────┼───────────────────────────────────┼──────────┼─────┤
│ guardops-quarantine-testfp12           │ default   │ Shell Spawned Inside Container    │ testfp12 │  2m │
╰────────────────────────────────────────┴───────────┴───────────────────────────────────┴──────────┴─────╯

  To manually release a pod after investigation:
   guardops quarantine-status --release <pod-name> --namespace <ns>
```

### Terraform module

```
infra/terraform/modules/alertmanager-webhook/
    main.tf       ServiceAccount, ClusterRole, ClusterRoleBinding, Deployment, Service
    variables.tf  project_name, environment, webhook_image (required); webhook_port, replicas (optional)
    outputs.tf    service_url, healthz_url, service_name, deployment_name, namespace
```

Enable in `terraform.tfvars`:
```hcl
enable_self_healing = true
webhook_image       = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/guardops-app:webhook-latest"
```

### Building the webhook image

```powershell
# Build Dockerfile.webhook (installs fastapi, uvicorn, pyyaml, kubectl into the app image)
cd D:\EXTRA\GuardOps
$ECR = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/guardops-app"
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin "123456789012.dkr.ecr.ap-south-1.amazonaws.com"
docker build -t "${ECR}:webhook-latest" -f Dockerfile.webhook .
docker push "${ECR}:webhook-latest"
```

### Testing quarantine manually

```powershell
# Port-forward the handler
kubectl port-forward svc/guardops-alertmanager-webhook 9095:9095 -n monitoring

# Fire a test quarantine webhook
$pod = kubectl get pods -n default --no-headers -o custom-columns=":metadata.name" | Select-Object -First 1
Invoke-WebRequest -Uri "http://localhost:9095/webhook" -Method POST `
    -ContentType "application/json" -UseBasicParsing -Body (ConvertTo-Json -Depth 10 @{
        receiver="guardops-webhook"; status="firing"
        alerts=@(@{status="firing"; fingerprint="testfp123"
            labels=@{alertname="GuardOpsFalcoCritical"; severity="critical"
                     guardops_action="quarantine"; pod=$pod; namespace="default"
                     rule="Shell Spawned Inside Container"}
            startsAt=(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
            endsAt="0001-01-01T00:00:00Z"})
        version="4"; groupKey="test"; truncatedAlerts=0
        groupLabels=@{}; commonLabels=@{}; commonAnnotations=@{}
        externalURL="http://alertmanager:9093"
    })

# Verify
guardops quarantine-status -n default
kubectl get networkpolicy -n default -l guardops.io/managed-by=guardops
kubectl get pods -n default -l guardops.io/quarantine=true
```

---

## Multi-Environment + Blue-Green (Phase 9)

Phase 9 adds staging/prod namespace separation and blue-green deploy support via a new `--slot` flag on `guardops deploy` and the `guardops switch` command.

### Environment config

Add an `environments` block to `.guardops.yaml`. Only keys that differ from the base config need to be specified:

```yaml
environments:
  staging:
    kubernetes:
      namespace: staging
    docker:
      image_tag_prefix: staging   # images tagged staging-
    security:
      tools:
        owasp_zap: false          # ZAP skipped in staging (no stable URL)
    helm:
      release_suffix: "-staging"  # release: guardops-app-staging
  prod:
    kubernetes:
      namespace: default
    docker:
      image_tag_prefix: ""        # images tagged  only
    security:
      tools:
        owasp_zap: true
    helm:
      release_suffix: ""          # release: guardops-app (backward compat)
```

### Blue-green workflow

```powershell
# Deploy both slots (can deploy in any order, both coexist)
guardops deploy --env staging --slot blue
guardops deploy --env staging --slot green

# Verify both are running
kubectl get pods -n staging --show-labels
helm list -n staging
# Expected: guardops-app-staging, guardops-app-staging-blue, guardops-app-staging-green

# Cut traffic to blue (creates shared traffic Service on first run)
guardops switch --slot blue --env staging

# Verify Service selector
kubectl get svc guardops-app -n staging -o jsonpath='{.spec.selector}'
# {"app.kubernetes.io/name":"guardops-app","guardops.io/slot":"blue"}

# Verify endpoints match blue pod IPs
kubectl get endpoints guardops-app -n staging
kubectl get pods -n staging -l "guardops.io/slot=blue" -o jsonpath='{.items[*].status.podIP}'

# Switch to green
guardops switch --slot green --env staging

# Roll back to blue (instant — no rebuild)
guardops switch --slot blue --env staging

# Preview changes without applying
guardops switch --slot green --env staging --dry-run
```

### How the traffic Service works

`guardops switch` creates (or patches) a single shared ClusterIP Service named after the project (`guardops-app`) in the target namespace. This Service is **not** owned by any Helm release — it is managed entirely by the switch command and can be identified by its labels:

```
guardops.io/managed-by: guardops
guardops.io/service-type: traffic
```

The selector uses two labels that are present on all slot pods:
- `app.kubernetes.io/name: guardops-app` — set by the Helm chart on all releases
- `guardops.io/slot: blue|green` — set when `blueGreen.enabled=true` in the Helm values

Switching is atomic: a single `kubectl apply` updates both the selector and the `guardops.io/active-slot` annotation. iptables propagation takes ~1-2 seconds.

---

## Security Pipeline

Four pre-deploy tools run in sequence, followed by one post-deploy DAST scan, one post-deploy runtime check, and continuous self-healing in production. All findings are normalised to a unified severity scale before gating.

| Tool | Phase | Type | What it catches | Severity mapping |
|------|-------|------|-----------------|-----------------|
| Semgrep | Pre-deploy | SAST | Code patterns, secrets, OWASP Top 10 | ERROR=HIGH, WARNING=MEDIUM, INFO=LOW |
| Bandit | Pre-deploy | SAST | Python-specific vulnerabilities | Adjusted by confidence level |
| Trivy (fs) | Pre-deploy | Secret/IaC | Hardcoded secrets, misconfigs | Direct |
| Trivy (image) | Pre-deploy | SCA | CVEs in OS packages and Python deps | UNKNOWN mapped to LOW |
| SonarQube | Pre-deploy | Quality gate | Security hotspots, code smells | BLOCKER=CRITICAL, CRITICAL=HIGH, MAJOR=MEDIUM |
| OWASP ZAP | Post-deploy | DAST | Runtime HTTP vulns, missing headers, exposed endpoints | High=CRITICAL, Medium=HIGH, Low=MEDIUM, Info=LOW |
| Falco (via Loki) | Post-deploy | Runtime | Shell spawns, file reads, package managers, root processes | Maps Falco priority to unified scale |
| Alertmanager webhook | Continuous | Self-healing | Automatic pod quarantine on CRITICAL Falco alert | CRITICAL triggers quarantine |

**Bandit confidence adjustment:**

| Severity | Confidence | Unified result |
|----------|-----------|----------------|
| HIGH | HIGH | CRITICAL |
| HIGH | LOW | MEDIUM |
| MEDIUM | HIGH | HIGH |
| LOW | HIGH | MEDIUM |

**ZAP severity is bumped one tier** vs SAST because a live runtime finding has a shorter exploit distance than a code pattern finding.

**Falco priority mapping:**

| Falco Priority | GuardOps Severity |
|---------------|------------------|
| EMERGENCY / ALERT / CRITICAL | CRITICAL |
| ERROR | HIGH |
| WARNING | MEDIUM |
| NOTICE / INFORMATIONAL / INFO / DEBUG | LOW |

Reports are written to `security/reports/latest.html` and `latest.json` after every scan. In CI, reports are uploaded to S3 automatically. ZAP reports are uploaded as the `zap-dast-report` artifact in every deploy run.

---

## GitHub OIDC (Phase 6)

Static IAM user credentials (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`) are fully replaced by GitHub OIDC. GitHub generates a short-lived JWT per workflow run; the CI runner exchanges it for temporary STS credentials valid for 1 hour. No long-lived secrets are stored anywhere.

**Required GitHub secrets (Phase 6+):**

| Secret | Value |
|--------|-------|
| `AWS_ROLE_ARN` | `terraform output github_actions_role_arn` |
| `AWS_ACCOUNT_ID` | Your 12-digit AWS account ID |
| `GUARDOPS_S3_BUCKET` | `terraform output s3_reports_bucket_name` |

**Required GitHub variables:**

| Variable | Value |
|----------|-------|
| `HAS_EKS_CLUSTER` | `true` (set after EKS is provisioned) |
| `HAS_AWS_ROLE` | `true` (set after OIDC terraform apply) |

**Optional secrets:**

| Secret | Purpose |
|--------|---------|
| `HAS_ZAP_ENABLED` | Set to `true` to enable ZAP DAST in CI deploy job |
| `HAS_FALCO_ENABLED` | Set to `true` to enable runtime gate in CI (Phase 7) |
| `SEMGREP_APP_TOKEN` | Semgrep cloud dashboard |
| `SONAR_TOKEN` | SonarQube |
| `SONAR_HOST_URL` | SonarQube |

---

## Configuration

GuardOps reads `.guardops.yaml` from your project directory.

```yaml
project:
  name: my-app            # Used as Helm release name, image name, ingress host
  cloud: local            # local | aws

kubernetes:
  namespace: default
  cluster: guardops-local

docker:
  registry: ""            # ECR URL for prod (e.g. 123.dkr.ecr.ap-south-1.amazonaws.com)

security:
  fail_on_severity: HIGH  # LOW | MEDIUM | HIGH | CRITICAL  (pre-deploy gate)
  skip_sonarqube: false

  # DAST (Phase 6)
  tools:
    owasp_zap: false      # set true in prod to enable post-deploy ZAP scan
  zap_target_url: ""      # leave empty to auto-detect from deploy result
  zap_fail_on: CRITICAL   # severity that blocks and triggers auto-rollback
  zap_timeout_seconds: 300

monitoring:
  grafana_url: ''
  prometheus_url: ''
  loki_url: 'http://localhost:3100'   # set after setup-runtime-security.ps1

# Phase 7 — Runtime Security
runtime_security:
  enabled: true                # set true after setup-runtime-security.ps1
  falco_alert_window: 1h       # default --since for runtime-status
  alert_fail_on: CRITICAL      # default --fail-on for CI gate
  namespaces_to_watch: []      # empty = query all namespaces

# Phase 8 — Self-Healing
self_healing:
  enabled: false               # set true after terraform apply with enable_self_healing=true
  webhook_url: ''              # set to terraform output webhook_service_url

# Phase 9 — Multi-Environment
environments:
  staging:
    kubernetes:
      namespace: staging
    docker:
      image_tag_prefix: staging
    security:
      tools:
        owasp_zap: false
    helm:
      release_suffix: "-staging"
  prod:
    kubernetes:
      namespace: default
    docker:
      image_tag_prefix: ""
    security:
      tools:
        owasp_zap: true
    helm:
      release_suffix: ""
```

---

## Commands Reference

### `guardops deploy`

```
Options:
  --env [local|staging|prod]
                         Target environment. Default: local
                         staging: deploys to staging namespace, tags image staging-<sha>,
                                  skips DAST, uses values-staging.yaml
                         prod:    deploys to default namespace, full DAST, values-prod.yaml
  --slot [blue|green]    Blue-green slot. Creates a slot-specific Helm release
                         (guardops-app-staging-blue) and labels pods with
                         guardops.io/slot=<slot>. Use guardops switch to cut traffic.
  --skip-scan            Skip security scans. Never use in prod.
  --skip-build           Reuse existing image.
  --skip-sonarqube       Skip SonarQube scan.
  --skip-trivy           Skip Trivy scans.
  --skip-dast            Skip OWASP ZAP DAST scan. Never use in prod.
  --fail-on [LOW|MEDIUM|HIGH|CRITICAL]
                         Severity threshold that blocks deploy. Default: HIGH
  --replicas INTEGER     Override replica count.
```

### `guardops switch` (Phase 9)

```
Options:
  --slot [blue|green]    Required. Slot to activate. All traffic will be routed
                         to pods labelled guardops.io/slot=<slot>.
  --env [local|staging|prod]
                         Environment to switch traffic in. Determines the default
                         namespace when --namespace is omitted. Default: staging
  --namespace, -n TEXT   Kubernetes namespace. Defaults to the namespace for --env.
  --service-name TEXT    Name of the shared traffic Service to patch.
                         Defaults to the project name from .guardops.yaml.
  --dry-run              Preview what would change without applying anything.
```

### `guardops scan`

Runs the full pre-deploy security scan pipeline without deploying. Writes HTML and JSON reports to `security/reports/`.

### `guardops rollback`

```
Options:
  --release TEXT         Helm release name. Default: reads from .guardops.yaml
  --revision INTEGER     Target revision. Default: 0 (previous release)
  --namespace TEXT       Kubernetes namespace. Default: default
```

### `guardops status`

Shows pod phase, readiness, restart count, node placement, and service URL for the deployed release.

### `guardops logs`

Streams logs from the running pod. Accepts `--tail` and `--follow` flags.

### `guardops runtime-status` (Phase 7)

```
Options:
  --since [15m|30m|1h|3h|6h|12h|24h|7d]
                         Time window to query Loki. Default: 1h
  --namespace TEXT       Filter alerts to a specific Kubernetes namespace.
  --severity [LOW|MEDIUM|HIGH|CRITICAL]
                         Minimum severity to display. Default: LOW (show all)
  --tail INTEGER         Maximum number of alerts to display. Default: 50
  --loki-url TEXT        Loki base URL. Overrides monitoring.loki_url in config.
                         Default: http://localhost:3100
  --fail-on [LOW|MEDIUM|HIGH|CRITICAL]
                         Exit 1 if alerts at or above this severity are found.
                         Intended for CI post-deploy gates.
```

**Prerequisites:** Loki must be reachable. Run `kubectl port-forward svc/loki 3100:3100 -n monitoring` first.

### `guardops quarantine-status` (Phase 8/9)

```
Options:
  --namespace, -n TEXT   Kubernetes namespace to check.
                         Defaults to the namespace for --env (if set), then
                         kubernetes.namespace in .guardops.yaml.
  --all-namespaces, -A   Check all namespaces (equivalent to kubectl -A).
  --env [local|staging|prod]
                         Environment to inspect. Determines default namespace
                         when --namespace is omitted. Has no effect with -A.
  --release TEXT         Manually release a quarantined pod by name.
                         Deletes its NetworkPolicy and removes quarantine label.
                         Requires a resolvable namespace.
  --json-output          Print raw JSON (useful for CI/scripts).
```

**Prerequisites:** kubectl must be configured and the cluster reachable.

---

## Local Kubernetes Setup (k3d)

```bash
# Create cluster with ingress port mapping
k3d cluster create guardops-local \
  --port "80:80@loadbalancer" \
  --port "443:443@loadbalancer" \
  --wait

# Install ingress-nginx
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace \
  --set controller.service.type=NodePort \
  --set controller.admissionWebhooks.enabled=false \
  --wait --timeout 5m

# Add to hosts file (Windows: C:\Windows\System32\drivers\etc\hosts)
# 127.0.0.1  test-app.local

# Access app after deploy
kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 8080:80
```

**Windows note:** After recreating a k3d cluster, patch the kubeconfig — replace `host.docker.internal` with `127.0.0.1`.

---

## AWS Infrastructure

Infrastructure is fully defined in `infra/terraform/`. Remote state is stored in S3 with DynamoDB locking — no local `.tfstate` files.

```
infra/terraform/
    bootstrap/      S3 bucket + DynamoDB table for remote state (run once)
    modules/
        ecr/        ECR repository, scan-on-push, 10-image lifecycle policy
        s3/         Reports bucket, versioning, AES256, Glacier after 90 days
        vpc/        Public + private subnets, NAT, IGW, route tables
        iam/        EKS cluster role, node role, AmazonEBSCSIDriverPolicy
        iam_oidc/   GitHub OIDC provider + CI role (Phase 6, replaces IAM user)
        eks/        Managed node group (t3.large), CoreDNS, kube-proxy,
                    VPC CNI, EBS CSI driver, launch template (IMDSv2 hop limit=2)
        falco/      Falco + Loki + Promtail via Helm provider (Phase 7)
                    Requires live EKS + monitoring namespace. Use
                    enable_runtime_security=true in terraform.tfvars.
                    Alternative: scripts/setup-runtime-security.ps1
        alertmanager-webhook/   Phase 8 self-healing handler (FastAPI pod)
                    ServiceAccount + ClusterRole + Deployment + ClusterIP Service
                    Requires live EKS + monitoring namespace + webhook image in ECR.
                    Use enable_self_healing=true in terraform.tfvars.
```

**Always-on (near-zero cost):** ECR, S3, DynamoDB, remote state bucket, OIDC provider, IAM role.

**Destroy nightly (~$5.28/day when running):** EKS control plane ($0.10/hr), t3.large node ($0.075/hr), NAT gateways ($0.045/hr each).

```powershell
# Bootstrap remote state (one-time only)
cd infra/terraform/bootstrap
terraform init && terraform apply -auto-approve

# Migrate existing state to S3
cd infra/terraform
terraform init -migrate-state

# Daily operations
terraform apply -auto-approve    # morning (~12 min)
terraform destroy -auto-approve  # evening (~8 min)
```

---

## Helm Chart

The Helm chart at `k8s/helm/guardops-app/` (v0.4.0) deploys with security defaults applied at the pod level:

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  readOnlyRootFilesystem: false
  capabilities:
    drop: ["ALL"]
```

Staging values (`values-staging.yaml`) add:
- `replicaCount: 1`
- `imagePullPolicy: Always`
- `config.ENVIRONMENT: staging`
- `monitoring.enabled: false` (set true once kube-prometheus-stack is confirmed running)

Production values (`values-prod.yaml`) add:
- `replicaCount: 2`
- `imagePullPolicy: Always`
- HPA enabled (CPU-based autoscaling, 2-10 replicas)
- Ingress with TLS configuration
- `monitoring.enabled: true` — creates ServiceMonitor for Prometheus scraping

Blue-green values (injected via `--set` by `guardops deploy --slot`):
- `blueGreen.enabled: true`
- `blueGreen.slot: blue|green` — adds `guardops.io/slot` label to pods and Deployment selector

---

## Dockerfile (Multi-stage)

Phase 4.5 replaced the single-stage build with a two-stage build:

```dockerfile
# Stage 1: builder — installs deps into an isolated venv
FROM python:3.11 AS builder
RUN python -m venv /build/venv
COPY requirements.txt .
RUN pip install -r requirements.txt

# Stage 2: runtime — copies only the venv, no pip/wheel/setuptools
FROM python:3.11-slim AS runtime
COPY --from=builder /build/venv /venv
COPY app.py .
RUN useradd --uid 10001 --no-create-home --shell /sbin/nologin appuser
USER 10001
```

Result: pip, wheel, and all build tools are absent from the final image, significantly reducing the CVE surface area reported by Trivy.

Phase 8 adds `Dockerfile.webhook`, which layers `fastapi`, `uvicorn`, `pyyaml`, `httpx`, `kubernetes`, and `kubectl` on top of the app image to produce the self-healing handler image.

---

## Development

```bash
# Clone and set up
git clone https://github.com/Bihan-Banerjee/GuardOps
cd GuardOps
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Run tests
pytest tests/ -v --tb=short

# Lint and type check
ruff check cli/ backend/
mypy cli/ backend/ --ignore-missing-imports
```

**Test coverage:**

| File | Tests | Covers |
|------|-------|--------|
| test_builder.py | 15 | Image naming, build success and failure paths, ECR tag format |
| test_config.py | 12 | YAML read/write, defaults, config existence checks |
| test_config_phase9.py | 20 | get_env_config, resolve_namespace, resolve_image_tag, resolve_helm_release_name |
| test_security.py | 68 | All 4 runners: skip, timeout, malformed JSON, severity mapping, report output |
| test_deployer.py | 37 | kubectl apply, k3d import, rollout wait, rollback, service URL |
| test_deployer_phase3.py | 35 | Helm deploy, rollback, release name sanitisation, chart path resolution |
| test_runtime_security.py | 53 | Falco priority mapping, FalcoQueryResult counts/filtering, Loki HTTP layer, --fail-on logic |

---

## Known Operational Notes

**State lock after interrupted apply:**
```powershell
# If terraform hangs on "Acquiring state lock":
terraform force-unlock -force 
```

**Subnet CIDR conflict after incomplete destroy:**
```powershell
# If terraform apply fails with InvalidSubnet.Conflict:
aws ec2 describe-subnets --filters "Name=cidrBlock,Values=10.0.1.0/24" `
    --query "Subnets[0].SubnetId" --output text
terraform import module.vpc.aws_subnet.public[1] 
terraform apply -auto-approve
```

**EBS CSI driver / IMDS hop limit:**
EKS AL2023 nodes default to IMDSv2 hop limit of 1, which blocks pod-level AWS SDK calls. The launch template in `modules/eks/main.tf` sets `http_put_response_hop_limit = 2` permanently. If the EBS CSI controller shows `CrashLoopBackOff` after a node replacement, verify the launch template is attached to the node group.

**Prometheus not scraping test-app:**
Always use `helm install` (not `helm upgrade --install`) for kube-prometheus-stack on a fresh cluster. Upgrading over a previous release can silently preserve stale `serviceMonitorNamespaceSelector` settings that restrict scraping to the `monitoring` namespace only.

**ZAP on Windows (local runs):**
`--network host` is not supported on Docker Desktop for Windows. The ZAP runner automatically omits this flag locally. Use `host.docker.internal` as the target hostname instead of `localhost` when port-forwarding a service for local DAST testing.

**Loki chart 6.x — important values quirks:**
- Use `storageClass: gp2` not `storageClassName: gp2` under `singleBinary.persistence`
- Must set `chunksCache.enabled: false` and `resultsCache.enabled: false` — defaults request ~11GB RAM, exceeding t3.large capacity
- Must set `read.replicas: 0`, `write.replicas: 0`, `backend.replicas: 0` or chart validation fails in SingleBinary mode

**Falco eBPF on EKS + t3.large:**
The Falco eBPF sensor requires kernel-level contiguous memory for perf ring buffer allocation. On a t3.large running the full monitoring stack, this allocation fails with `unable to mmap the perf-buffer`. The Falco simulator CronJob (`k8s/falco/`) provides identical JSON output for portfolio/dev use. See `infra/terraform/modules/falco/` for production deployment documentation.

**HCL multi-line strings:**
HCL does not support Python-style implicit string concatenation across lines inside parentheses. All `description` values in Terraform files must be single-line strings. The `+` operator is also not valid for string concatenation in HCL — use interpolation (`"${var.a}.${var.b}"`) instead.

**Terraform identity change error after manual kubectl deletes:**
If resources are deleted outside Terraform (e.g. `kubectl delete deployment`) and then `terraform apply` throws `Unexpected Identity Change`, run:
```powershell
terraform state rm "module.alertmanager_webhook[0].kubernetes_deployment.webhook"
terraform apply -auto-approve
```

**Webhook image uses /venv/bin/python, not system Python:**
The app image's CMD uses `/venv/bin/python` (an isolated virtualenv). All pip installs for the webhook handler in `Dockerfile.webhook` must target the venv: `RUN /venv/bin/python -m pip install ...`. Installing via `/usr/local/bin/pip` writes to a different site-packages that the venv Python cannot see.

**kubectl --short flag removed in v1.28+:**
The `readyz` endpoint in `alertmanager_handler.py` calls `kubectl version --client`. If your handler image uses kubectl v1.27 or earlier, remove `--short` from that call — the flag was removed and causes a non-zero exit code that makes `/readyz` return 503.

**setup-observability.ps1 must run from repo root:**
The script uses relative paths to `k8s/observability/`. Running it from inside `scripts\` resolves to `scripts\k8s\observability\...` which does not exist. Always run from `D:\EXTRA\GuardOps`: `.\scripts\setup-observability.ps1`. The `ServiceMonitor` CRD that kube-prometheus-stack installs is also required by the Helm chart's `servicemonitor.yaml` template — if the chart deploy fails with `no matches for kind "ServiceMonitor"`, it means the observability stack was not installed first.

**monitoring.enabled in values-staging.yaml:**
Set to `false` on a fresh cluster before kube-prometheus-stack is installed, to avoid the duplicate port warning and ServiceMonitor CRD dependency. Flip to `true` once the stack is confirmed running.

**Blue-green traffic Service not owned by Helm:**
The shared `guardops-app` Service in the staging namespace is created by `guardops switch`, not by any Helm release. It will not appear in `helm list` and will not be deleted by `helm uninstall`. To clean it up manually: `kubectl delete svc guardops-app -n staging`.

**Nightly shutdown order matters:**
```powershell
helm uninstall kube-prometheus-stack -n monitoring  
helm uninstall loki -n monitoring
helm uninstall promtail -n monitoring
kubectl delete pvc --all -n monitoring             
Start-Sleep -Seconds 30                             
cd infra/terraform && terraform destroy -auto-approve
```
Skipping the Helm uninstall leaves orphaned EBS volumes that persist after `terraform destroy` and continue billing silently.

---

## Release History

| Version | Status | Description |
|---------|--------|-------------|
| v0.1.0 | Published | CLI scaffold, k3d local deploy via kubectl |
| v0.2.0 | Published | Security scanning pipeline, 154 tests, GitHub Actions CI |
| v0.3.0 | Published | Helm deploy, rollback command, EKS Terraform modules |
| v0.4.0 | Published | Full AWS pipeline: ECR push, EKS deploy, S3 report upload |
| v0.4.1 | Published | Remote TF state, multi-stage Docker, subprocess hardening, Trivy CI cache |
| v0.5.0 | Published | Prometheus + Grafana, `/metrics` endpoint, ServiceMonitor, EBS CSI, custom metrics |
| v0.6.0 | Published | GitHub OIDC replaces static IAM keys, no more AWS_ACCESS_KEY_ID in CI |
| v0.6.1 | Published | OWASP ZAP DAST post-deploy scan, auto-rollback on CRITICAL, ZAP image fix (ghcr.io), Windows Docker compat |
| v0.7.0 | Published | Loki + Promtail log pipeline, Falco-format alert ingestion, `guardops runtime-status` CLI, CI runtime gate job, Falco rules + simulator |
| v0.8.0 | Published | Self-healing: Alertmanager webhook handler, automatic pod quarantine via NetworkPolicy, auto-release on alert resolved, `guardops quarantine-status` CLI, Terraform alertmanager-webhook module, Dockerfile.webhook, PrometheusRule + AlertmanagerConfig wiring |
| v0.9.0 | **Current** | Multi-environment: staging + prod namespace separation, `guardops deploy --env staging`, blue-green deploy with `--slot blue/green`, `guardops switch` traffic cutover, environment-scoped config helpers, `staging-<sha>` ECR image tags, `--env` flag on quarantine-status, Helm chart v0.4.0 with blueGreen values |

---

## Planned Future Updates

| Version | Status | Description |
|---------|--------|-------------|
| v1.0.0 | Planned | Real domain, TLS via cert-manager, ArgoCD GitOps, full runbooks |
| v1.1.0 | Planned | SBOM + Cosign signing |
| v1.2.0 | Planned | Kyverno admission control |
| v1.3.0 | Planned | Scan metadata database |
| v1.4.0 | Planned | Web dashboard |
| v1.5.0 | Planned | Vulnerability waivers |
| v1.6.0 | Planned | LLM-assisted triage |
| v1.7.0 | Planned | Risk-based scoring (scoped) |

---

## License

MIT
# GuardOps

> Production-grade DevSecOps CLI. Build, scan, and deploy with security gates at every stage.

[![PyPI version](https://img.shields.io/pypi/v/guardops.svg)](https://pypi.org/project/guardops/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/Bihan-Banerjee/GuardOps/actions/workflows/ci.yaml/badge.svg)](https://github.com/Bihan-Banerjee/GuardOps/actions)

GuardOps wraps a complete secure delivery pipeline behind a single command. Given any application repo, it builds a Docker image, runs four security scanners in sequence, deploys to Kubernetes via Helm, and exposes live metrics to Prometheus — blocking the pipeline if HIGH or CRITICAL findings are detected.

```
guardops deploy --env prod
```

That one command: builds a multi-stage Docker image, runs Semgrep + Bandit + Trivy + SonarQube, pushes to ECR, deploys to EKS via Helm with automatic rollback on failure, runs OWASP ZAP DAST against the live application with auto-rollback on CRITICAL findings, and exposes `/metrics` to a live Grafana dashboard.

---

## Current Status — v0.6.1

| Phase | Version | Status | What was built |
|-------|---------|--------|----------------|
| 1 — CLI + Local Deploy | v0.1.0 | ✅ Done | Click CLI framework, k3d local deploy, Docker build |
| 2 — Security Scanning + CI | v0.2.0 | ✅ Done | Semgrep, Bandit, Trivy, SonarQube, 154 tests, GitHub Actions |
| 3 — Helm + EKS Infrastructure | v0.3.0 | ✅ Done | Helm deploy, rollback command, Terraform VPC/EKS/IAM/ECR/S3 |
| 4 — Full AWS Pipeline | v0.4.0 | ✅ Done | ECR push, EKS deploy, S3 report upload, cost-optimised infra |
| 4.5 — Reliability Hardening | v0.4.1 | ✅ Done | Remote Terraform state (S3+DynamoDB), multi-stage Docker build, subprocess timeout+encoding fixes, Trivy DB cache in CI |
| 5 — Observability | v0.5.0 | ✅ Done | Prometheus + Grafana via kube-prometheus-stack, `/metrics` endpoint, ServiceMonitor, EBS CSI driver, custom dashboards |
| 6 — Security Hardening | v0.6.1 | ✅ Done | GitHub OIDC replaces IAM user (no more static keys), DAST via OWASP ZAP with post-deploy scan and auto-rollback on CRITICAL |

---

## Roadmap

| Phase | Target | What it adds |
|-------|--------|-------------|
| 7 — Runtime Security | v0.7.0 | Falco with eBPF, custom rules (alert on shell spawn inside pod), alerts routed to Loki |
| 8 — Self-Healing | v0.8.0 | Alertmanager webhook receiver, automatic NetworkPolicy quarantine on Falco alert, node drain on resource exhaustion |
| 9 — Multi-Environment | v0.9.0 | Staging + prod namespaces, blue-green deploy strategy, `guardops switch --slot green` |
| 10 — Full Production | v1.0.0 | Real domain + TLS via cert-manager, ArgoCD GitOps, runbook documentation |

---

## Architecture

```
Developer
    |
    v
guardops deploy
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
    |       local: k3d image import                     |
    |       prod:  docker push -> AWS ECR               |
    |                                                   |
    +-- Step 4: Helm Deploy ───────────────────────────+
    |       helm upgrade --install --atomic             |
    |       local: k3d + values.yaml                   |
    |       prod:  EKS + values-prod.yaml              |
    |       Automatic rollback on timeout or error      |
    |                                                   |
    +-- Step 5: DAST (Phase 6) ────────────────────────+
            OWASP ZAP baseline scan (passive)
            Target: live deployed application
            BLOCKED + auto-rollback if CRITICAL found
            Report written to security/reports/
            Skipped for local env (no stable URL)
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
|    +-- guardops-app Pods (x2)                                    |
|    |    +-- /healthz, /ready, /metrics endpoints                 |
|    |    +-- port 8080, non-root UID 10001                        |
|    |    +-- capabilities.drop ALL                                |
|    |                                                             |
|    +-- monitoring namespace                                      |
|         +-- Prometheus  (kube-prometheus-stack)                  |
|         +-- Grafana     (pre-loaded dashboards)                  |
|         +-- Alertmanager                                         |
|         +-- kube-state-metrics, node-exporter                    |
|                                                                  |
|  ECR: guardops-app (scan-on-push, 10-image lifecycle)            |
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
    pytest (167+ tests) + ruff + mypy
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
Job 5: upload-reports      <-- always runs
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

# Build, scan, push to ECR, deploy to EKS
guardops deploy --env prod

# Skip SonarQube if not configured
guardops deploy --env prod --skip-sonarqube

# Skip DAST scan (dev only)
guardops deploy --env prod --skip-dast

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

### Setup (morning start)

```powershell
# After terraform apply and kubectl configure:
.\scripts\setup-observability.ps1
```

### Shutdown (nightly — prevents orphaned EBS volumes)

```powershell
helm uninstall kube-prometheus-stack -n monitoring
kubectl delete pvc --all -n monitoring
Start-Sleep -Seconds 30
.\scripts\night-shutdown.ps1
```

---

## Security Pipeline

Four pre-deploy tools run in sequence, followed by one post-deploy DAST scan. All findings are normalised to a unified severity scale before gating.

| Tool | Phase | Type | What it catches | Severity mapping |
|------|-------|------|-----------------|-----------------|
| Semgrep | Pre-deploy | SAST | Code patterns, secrets, OWASP Top 10 | ERROR=HIGH, WARNING=MEDIUM, INFO=LOW |
| Bandit | Pre-deploy | SAST | Python-specific vulnerabilities | Adjusted by confidence level |
| Trivy (fs) | Pre-deploy | Secret/IaC | Hardcoded secrets, misconfigs | Direct |
| Trivy (image) | Pre-deploy | SCA | CVEs in OS packages and Python deps | UNKNOWN mapped to LOW |
| SonarQube | Pre-deploy | Quality gate | Security hotspots, code smells | BLOCKER=CRITICAL, CRITICAL=HIGH, MAJOR=MEDIUM |
| OWASP ZAP | Post-deploy | DAST | Runtime HTTP vulns, missing headers, exposed endpoints | High=CRITICAL, Medium=HIGH, Low=MEDIUM, Info=LOW |

**Bandit confidence adjustment:**

| Severity | Confidence | Unified result |
|----------|-----------|----------------|
| HIGH | HIGH | CRITICAL |
| HIGH | LOW | MEDIUM |
| MEDIUM | HIGH | HIGH |
| LOW | HIGH | MEDIUM |

**ZAP severity is bumped one tier** vs SAST because a live runtime finding has a shorter exploit distance than a code pattern finding.

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
```

---

## Commands Reference

### `guardops deploy`

```
Options:
  --env [local|prod]     Target environment. Default: local
  --skip-scan            Skip security scans. Never use in prod.
  --skip-build           Reuse existing image.
  --skip-sonarqube       Skip SonarQube scan.
  --skip-trivy           Skip Trivy scans.
  --skip-dast            Skip OWASP ZAP DAST scan. Never use in prod.
  --fail-on [LOW|MEDIUM|HIGH|CRITICAL]
                         Severity threshold that blocks deploy. Default: HIGH
  --replicas INTEGER     Override replica count.
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

The Helm chart at `k8s/helm/guardops-app/` deploys with security defaults applied at the pod level:

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  readOnlyRootFilesystem: false
  capabilities:
    drop: ["ALL"]
```

Production values (`values-prod.yaml`) add:
- `replicaCount: 2`
- `imagePullPolicy: Always`
- HPA enabled (CPU-based autoscaling, 2-10 replicas)
- Ingress with TLS configuration
- `monitoring.enabled: true` — creates ServiceMonitor for Prometheus scraping

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
| test_security.py | 68 | All 4 runners: skip, timeout, malformed JSON, severity mapping, report output |
| test_deployer.py | 37 | kubectl apply, k3d import, rollout wait, rollback, service URL |
| test_deployer_phase3.py | 35 | Helm deploy, rollback, release name sanitisation, chart path resolution |

---

## Known Operational Notes

**State lock after interrupted apply:**
```powershell
# If terraform hangs on "Acquiring state lock":
aws dynamodb scan --table-name guardops-tf-lock --region ap-south-1 --query "Items[0].Info.S" --output text
# Copy the ID field from the output, then:
terraform force-unlock -force <ID>
```

**EBS CSI driver / IMDS hop limit:**
EKS AL2023 nodes default to IMDSv2 hop limit of 1, which blocks pod-level AWS SDK calls. The launch template in `modules/eks/main.tf` sets `http_put_response_hop_limit = 2` permanently. If the EBS CSI controller shows `CrashLoopBackOff` after a node replacement, verify the launch template is attached to the node group.

**Prometheus not scraping test-app:**
Always use `helm install` (not `helm upgrade --install`) for kube-prometheus-stack on a fresh cluster. Upgrading over a previous release can silently preserve stale `serviceMonitorNamespaceSelector` settings that restrict scraping to the `monitoring` namespace only.

**ZAP on Windows (local runs):**
`--network host` is not supported on Docker Desktop for Windows. The ZAP runner automatically omits this flag locally. Use `host.docker.internal` as the target hostname instead of `localhost` when port-forwarding a service for local DAST testing.

**Nightly shutdown order matters:**
```powershell
helm uninstall kube-prometheus-stack -n monitoring  # triggers EBS volume deletion
kubectl delete pvc --all -n monitoring              # ensures PVCs are removed
Start-Sleep -Seconds 30                             # wait for ec2:DeleteVolume
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
| v0.6.1 | Current | OWASP ZAP DAST post-deploy scan, auto-rollback on CRITICAL, ZAP image fix (ghcr.io), Windows Docker compat |
| v0.7.0 | Planned | Falco runtime security, eBPF, alert routing to Loki |
| v0.8.0 | Planned | Self-healing: Alertmanager webhook → NetworkPolicy quarantine, node drain |
| v0.9.0 | Planned | Multi-environment: staging + prod namespaces, blue-green deploy |
| v1.0.0 | Planned | Real domain, TLS, ArgoCD GitOps, full runbooks |

---

## License

MIT
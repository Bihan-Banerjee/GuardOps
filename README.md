# GuardOps

> Production-grade DevSecOps CLI. Build, scan, and deploy with security gates at every stage.

[![PyPI version](https://img.shields.io/pypi/v/guardops.svg)](https://pypi.org/project/guardops/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/Bihan-Banerjee/GuardOps/actions/workflows/ci.yaml/badge.svg)](https://github.com/Bihan-Banerjee/GuardOps/actions)

GuardOps wraps a complete secure delivery pipeline behind a single command. Given any application repo, it builds a Docker image, runs four security scanners in sequence, and deploys to Kubernetes, blocking the pipeline if HIGH or CRITICAL findings are detected.

```
guardops deploy --env prod
```

That one command: builds the image, runs Semgrep + Bandit + Trivy + SonarQube, pushes to ECR, and deploys to EKS via Helm with automatic rollback on failure.

---

## Architecture

```
Developer
    |
    v
guardops deploy
    |
    +-- Step 1: Docker Build -------------------------+
    |       docker build -t <name>:<git-sha> .        |
    |                                                 |
    +-- Step 2: Security Scans ----------------------+
    |       Semgrep    (SAST, code patterns)          |
    |       Bandit     (Python-specific vulns)        |
    |       Trivy fs   (secrets, IaC misconfigs)      |
    |       Trivy img  (CVEs in OS + deps)            |
    |       SonarQube  (quality gate, optional)       |
    |                                                 |
    |       BLOCKED if any finding >= HIGH            |
    |       Report written to security/reports/       |
    |                                                 |
    +-- Step 3: Registry Push -----------------------+
    |       local: k3d image import                   |
    |       prod:  docker push -> AWS ECR             |
    |                                                 |
    +-- Step 4: Helm Deploy -------------------------+
            helm upgrade --install --atomic
            local: k3d + values.yaml
            prod:  EKS + values-prod.yaml
            Automatic rollback on timeout or error
```

### Infrastructure (AWS, Terraform-managed)

```
ap-south-1 (Mumbai)
+----------------------------------------------------------+
|  VPC  10.0.0.0/16                                        |
|                                                          |
|  Public Subnets (ap-south-1a, ap-south-1b)              |
|    NAT Gateways, Load Balancers                          |
|                                                          |
|  Private Subnets (ap-south-1a, ap-south-1b)             |
|    EKS Managed Node Group (t3.medium)                    |
|    +-- guardops-app Pod                                  |
|         +-- /healthz endpoint                            |
|         +-- port 8080                                    |
|         +-- non-root user, readOnlyRootFilesystem        |
|         +-- capabilities.drop ALL                        |
|                                                          |
|  ECR: guardops-app (scan-on-push, 10-image lifecycle)    |
|  S3:  guardops-reports-* (scan reports, versioned)       |
+----------------------------------------------------------+
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
    Semgrep + Bandit -- gates on HIGH+
    |
    v
Job 3: container-scan
    Docker build + Trivy -- gates on fixable HIGH/CRITICAL
    ECR push (if AWS creds present)
    |
    v
Job 4: deploy              <-- active when HAS_EKS_CLUSTER=true
    helm upgrade --install --atomic --timeout 5m
    kubectl rollout status verify
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

# Skip sonarqube if not configured
guardops deploy --env prod --skip-sonarqube

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
  fail_on_severity: HIGH  # LOW | MEDIUM | HIGH | CRITICAL
  skip_sonarqube: false
```

---

## Security Pipeline

Four tools run in sequence. All findings are normalized to a unified severity scale before gating.

| Tool | Type | What it catches | Severity mapping |
|------|------|-----------------|-----------------|
| Semgrep | SAST | Code patterns, secrets, OWASP Top 10 | ERROR=HIGH, WARNING=MEDIUM, INFO=LOW |
| Bandit | SAST | Python-specific vulnerabilities | Adjusted by confidence level |
| Trivy (fs) | Secret/IaC | Hardcoded secrets, misconfigs | Direct |
| Trivy (image) | SCA | CVEs in OS packages and Python deps | UNKNOWN mapped to LOW |
| SonarQube | Quality gate | Security hotspots, code smells | BLOCKER=CRITICAL, CRITICAL=HIGH, MAJOR=MEDIUM |

**Bandit confidence adjustment:**

| Severity | Confidence | Unified result |
|----------|-----------|----------------|
| HIGH | HIGH | CRITICAL |
| HIGH | LOW | MEDIUM |
| MEDIUM | HIGH | HIGH |
| LOW | HIGH | MEDIUM |

Reports are written to `security/reports/latest.html` and `latest.json` after every scan. In CI, reports are uploaded to S3 automatically.

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
  --fail-on [LOW|MEDIUM|HIGH|CRITICAL]
                         Severity threshold that blocks deploy. Default: HIGH
  --replicas INTEGER     Override replica count.
```

### `guardops scan`

Runs the full security scan pipeline without deploying. Writes HTML and JSON reports to `security/reports/`.

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

# Add to /etc/hosts (or Windows hosts file)
# 127.0.0.1  test-app.local

# Access app after deploy
kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 8080:80
```

**Windows note:** After recreating a k3d cluster, patch the kubeconfig: replace `host.docker.internal` with `127.0.0.1`.

---

## AWS Infrastructure

Infrastructure is fully defined in `infra/terraform/`. Apply with `terraform apply` from that directory.

```
infra/terraform/
    modules/
        ecr/        ECR repository, scan-on-push, 10-image lifecycle policy
        s3/         Reports bucket, versioning, AES256, Glacier after 90 days
        vpc/        Public + private subnets, NAT, IGW, route tables
        iam/        EKS cluster role, node role, CI user (least-privilege)
        eks/        Managed node group, CoreDNS, kube-proxy, VPC CNI
```

ECR and S3 are free-tier safe and can remain provisioned permanently. VPC, IAM, and EKS cost roughly $4.50/day and should be destroyed when not in use:

```bash
terraform destroy   # stops all charges
```

---

## Helm Chart

The Helm chart at `k8s/helm/guardops-app/` deploys with security defaults applied at the pod level:

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
```

Production values (`values-prod.yaml`) add:
- `replicaCount: 2`
- `imagePullPolicy: Always`
- HPA enabled (CPU-based autoscaling)
- Ingress with TLS configuration

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
| test_deployer_phase3.py | 35 | Helm deploy, rollback, release name sanitization, chart path resolution |

---

## Release History

| Version | Status | Description |
|---------|--------|-------------|
| v0.1.0 | Published | CLI scaffold, k3d local deploy via kubectl |
| v0.2.0 | Published | Security scanning pipeline, 154 tests, GitHub Actions CI |
| v0.3.0 | Published | Helm deploy, rollback command, EKS Terraform modules |
| v0.4.0 | Current | Full AWS pipeline: ECR push, EKS deploy, S3 report upload, cost-optimized infra |
| v0.5.0 | Planned | Prometheus + Grafana observability, /metrics endpoint |
| v1.0.0 | Planned | Multi-environment (staging/prod), blue-green deploy strategy, OIDC auth |

---

## License

MIT

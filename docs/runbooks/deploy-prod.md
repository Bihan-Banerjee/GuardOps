# Runbook: Production Deploy

**Phase 10 — GuardOps v1.0.0**  
**Path:** `docs/runbooks/deploy-prod.md`

---

## Overview

Every prod deploy follows a 7-step pipeline. Steps 1–3 are automated in CI.
Steps 4–7 run via the CI job graph and are monitored here.

```
[1: build + scan] → [2: sast-report] → [3: push-ecr]
  → [4: deploy-prod] → [5: runtime-gate] → [6: dast-gate] → [7: sync-gate]
```

---

## Pre-Deploy Checklist

Run this before pushing to `main`:

- [ ] `kubectl get nodes` — all nodes Ready
- [ ] `kubectl get pods -n default` — no CrashLoopBackOff, no Pending
- [ ] `kubectl get pods -n monitoring` — Prometheus, Grafana, Alertmanager all Running
- [ ] `guardops runtime-status --env prod` — no CRITICAL Falco alerts in last 1h
- [ ] `guardops sync-status --env prod` — ArgoCD is Synced + Healthy (not mid-sync from a previous deploy)
- [ ] EKS cluster is running: `aws eks describe-cluster --name guardops-prod-cluster --query 'cluster.status'`

---

## Deploy Steps

### Step 1 — Push to main

```bash
git add .
git commit -m "feat: <description>"
git push origin main
```

CI triggers automatically on push to `main`. Monitor at:
`https://github.com/Bihan-Banerjee/GuardOps/actions`

---

### Step 2 — Watch Job 1: Build & Scan

Expected duration: **3–5 min**

Gates: Semgrep, Bandit, Trivy filesystem, Trivy image — all must pass with zero HIGH+ findings.

If this job fails:
```bash
guardops scan --fail-on HIGH   # run locally to see findings
```
Fix findings, push again. Do not use `--skip-scan` in prod.

---

### Step 3 — Watch Job 3: ECR Push

Expected duration: **1–2 min**

The image is tagged with the short git SHA (e.g. `abc1234`) and pushed to:
```
<account>.dkr.ecr.ap-south-1.amazonaws.com/guardops-prod:<sha>
```

Verify in AWS Console: ECR → `guardops-prod` → Images tab.

---

### Step 4 — Watch Job 4: Deploy (prod)

Expected duration: **2–4 min**

This job:
1. Runs `guardops deploy --env prod --skip-scan --skip-dast` (direct Helm upgrade)
2. Writes `values-override-prod.yaml` with the new image tag
3. Commits and pushes the override file (`[skip ci]` commit)
4. Triggers an ArgoCD sync for `guardops-app-prod`

Verify the Helm release updated:
```bash
helm history guardops-app -n default
kubectl rollout status deployment/guardops-app -n default --timeout=120s
```

---

### Step 5 — Watch Job 5: Runtime Gate

Expected duration: **1 min**

Checks Falco for CRITICAL alerts in the last 15 minutes. Fails the pipeline if any
CRITICAL runtime events occurred during or immediately after the Helm rollout.

If this fails:
```bash
guardops runtime-status --env prod --window 30m
kubectl logs -n monitoring -l app=falco --tail=100
```

---

### Step 6 — Watch Job 6: DAST (ZAP)

Expected duration: **3–5 min**

OWASP ZAP runs a passive baseline scan against `https://guardops.dev`. If CRITICAL
findings are detected, the deploy job auto-rolls back and exits 1.

Download the ZAP report from the GitHub Actions artifact tab:
`zap-report-<run_id>.html`

If ZAP finds something new:
```bash
guardops rollback --env prod          # if not auto-rolled back
# Fix the vulnerability in code, push a new commit
```

---

### Step 7 — Watch Job 7: ArgoCD Sync Gate

Expected duration: **up to 5 min**

Polls `guardops sync-status --env prod --wait --timeout 300` until ArgoCD reports
Synced + Healthy. This confirms ArgoCD has reconciled the GitOps commit and the
cluster state matches the override file.

Check manually at any point:
```bash
guardops sync-status --env prod
# or
argocd app get guardops-app-prod
```

ArgoCD UI: `https://argocd.guardops.dev/applications/guardops-app-prod`

---

## Post-Deploy Verification

```bash
# Pod health
kubectl get pods -n default
kubectl describe pod -n default -l app.kubernetes.io/name=guardops-app

# Endpoint check
curl -I https://guardops.dev/healthz        # expect HTTP 200
curl -I https://guardops.dev/ready          # expect HTTP 200

# Live metrics
open https://grafana.guardops.dev           # GuardOps dashboard

# ArgoCD final state
guardops sync-status --env prod
```

---

## If the Pipeline Fails

| Job failed          | First action                                          |
|---------------------|-------------------------------------------------------|
| Build & Scan (1)    | `guardops scan --fail-on HIGH` locally, fix findings  |
| ECR Push (3)        | Check AWS credentials: `aws sts get-caller-identity`  |
| Deploy (4)          | `helm history guardops-app -n default`                |
| Runtime Gate (5)    | `guardops runtime-status --env prod --window 30m`     |
| DAST (6)            | Download ZAP artifact, review findings, `guardops rollback` |
| Sync Gate (7)       | `guardops sync-status --env prod`, check ArgoCD UI    |

Emergency rollback:
```bash
guardops rollback --env prod
# or
argocd app rollback guardops-app-prod
```

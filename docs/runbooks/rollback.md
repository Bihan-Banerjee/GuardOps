# Runbook: Rollback

**Phase 10 — GuardOps v1.0.0**  
**Path:** `docs/runbooks/rollback.md`

---

## When to Roll Back

Roll back immediately if any of the following are true after a deploy:

- `kubectl get pods -n default` shows CrashLoopBackOff or ImagePullBackOff
- `https://guardops.live/healthz` returns non-200
- Grafana shows error rate spike (> 1% 5xx responses)
- `guardops runtime-status --env prod` reports CRITICAL Falco alerts
- `guardops sync-status --env prod` reports Degraded

---

## Method 1 — GuardOps CLI (fastest)

```bash
guardops rollback --env prod
```

This runs `helm rollback guardops-app` to the previous Helm revision.
Kubernetes performs a rolling update back to the prior image — zero downtime.

Verify:
```bash
helm history guardops-app -n default          # confirm revision decremented
kubectl rollout status deployment/guardops-app -n default --timeout=60s
curl -I https://guardops.live/healthz
```

---

## Method 2 — Direct Helm Rollback

Use when `guardops rollback` is unavailable (e.g. broken CLI install).

```bash
# List available revisions
helm history guardops-app -n default

# Roll back to specific revision (e.g. revision 5)
helm rollback guardops-app 5 -n default --wait --timeout 120s

# Or roll back one revision
helm rollback guardops-app -n default
```

---

## Method 3 — ArgoCD Rollback (GitOps)

Use when the prod issue was introduced by a GitOps commit (i.e. ArgoCD applied
a bad values-override-prod.yaml).

**Via CLI:**
```bash
# List deployment history
argocd app history guardops-app-prod

# Roll back to a specific revision number (from history output)
argocd app rollback guardops-app-prod <revision-number>
```

**Via UI:**
1. Open `https://argocd.guardops.live/applications/guardops-app-prod`
2. Click **History and Rollback**
3. Select the last known-good revision
4. Click **Rollback**

**Via Git revert (cleanest):**
```bash
# Revert the values-override-prod.yaml commit
git log --oneline --follow k8s/helm/guardops-app/values-override-prod.yaml

# Revert the bad commit
git revert <bad-commit-sha> --no-edit
git push origin main

# ArgoCD auto-syncs staging; trigger prod sync manually:
guardops sync-status --env prod --wait
```

---

## Method 4 — Blue-Green Instant Rollback

If the deploy used blue-green slots, rollback is instant traffic switch — no
pod restart required.

```bash
# If green is broken, switch back to blue immediately
guardops switch --slot blue --env prod

# Verify traffic is back on blue
kubectl get svc guardops-app-traffic -n default -o jsonpath='{.spec.selector}'
```

---

## Post-Rollback Checklist

- [ ] `kubectl get pods -n default` — all pods Running
- [ ] `curl -I https://guardops.live/healthz` — HTTP 200
- [ ] `guardops sync-status --env prod` — Synced + Healthy
- [ ] Grafana error rate back to baseline
- [ ] Notify team in Slack: "Rolled back to revision X — investigating root cause"
- [ ] File a post-mortem issue in GitHub

---

## Staging Rollback

```bash
guardops rollback --env staging
# or
argocd app rollback guardops-app-staging <revision>
```

Staging uses auto-sync — disable it first if you want ArgoCD to stop
re-applying the broken commit:
```bash
argocd app set guardops-app-staging --sync-policy none
# Fix the issue, then re-enable:
argocd app set guardops-app-staging --sync-policy automated
```

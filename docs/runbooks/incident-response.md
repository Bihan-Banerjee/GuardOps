# Runbook: Incident Response

**Phase 10 — GuardOps v1.0.0**  
**Path:** `docs/runbooks/incident-response.md`

---

## Severity Levels

| Level    | Example Falco alert                        | Response time | Auto-quarantine? |
|----------|--------------------------------------------|---------------|-----------------|
| CRITICAL | Shell spawned in container, k8s secret read | Immediate     | Yes             |
| HIGH     | Unexpected outbound connection              | < 15 min      | No              |
| MEDIUM   | Sensitive file opened                       | < 1 hour      | No              |
| LOW      | File below /etc opened                      | Next business day | No           |

---

## Phase 1 — Detection

GuardOps surfaces runtime alerts in two ways:

**Terminal (on-demand):**
```bash
guardops runtime-status --env prod
guardops runtime-status --env prod --window 30m --fail-on HIGH
```

**Alertmanager webhook (automatic):**
CRITICAL Falco alerts trigger the Phase 8 webhook, which automatically quarantines
the offending pod by patching a `guardops.io/quarantined: "true"` label and removing
it from the Service's selector.

**Check quarantine state:**
```bash
guardops quarantine-status --env prod
kubectl get pods -n default -l guardops.io/quarantined=true
```

---

## Phase 2 — Immediate Containment

If a CRITICAL alert fires and auto-quarantine did not trigger (e.g. the webhook
is down), quarantine manually:

```bash
# Find the offending pod
kubectl get pods -n default
kubectl describe pod <pod-name> -n default

# Manual quarantine: remove pod from Service selector
kubectl label pod <pod-name> -n default guardops.io/quarantined=true --overwrite
kubectl patch pod <pod-name> -n default \
  -p '{"metadata":{"labels":{"app.kubernetes.io/instance": "quarantined"}}}'
```

Verify the pod is no longer receiving traffic:
```bash
kubectl get endpoints guardops-app -n default
# The quarantined pod's IP should not appear in the Endpoints list
```

---

## Phase 3 — Investigation

**Collect Falco alert details:**
```bash
guardops runtime-status --env prod --window 2h
kubectl logs -n monitoring -l app=falco --tail=500 | grep CRITICAL
```

**Collect pod logs from the offending pod:**
```bash
kubectl logs <pod-name> -n default --previous --tail=200
kubectl logs <pod-name> -n default --tail=200
```

**Inspect pod filesystem (if still running):**
```bash
kubectl exec -it <pod-name> -n default -- /bin/sh
# Or for a read-only investigation:
kubectl debug <pod-name> -n default --image=busybox --target=<container-name>
```

**Check Loki for correlated logs:**
```bash
# Port-forward Grafana
kubectl port-forward svc/kube-prometheus-stack-grafana -n monitoring 3000:80

# Open: http://localhost:3000 → Explore → Loki
# Query: {namespace="default", pod="<pod-name>"}
```

**Check for lateral movement:**
```bash
# Any other pods with unusual activity in the same window?
guardops runtime-status --env prod --window 2h

# Check NetworkPolicy — is the pod talking to unexpected IPs?
kubectl get networkpolicy -n default
```

---

## Phase 4 — Eradication

**Option A — Roll back the release:**
```bash
guardops rollback --env prod
```

**Option B — Scale down only the affected deployment:**
```bash
kubectl scale deployment guardops-app -n default --replicas=0
kubectl scale deployment guardops-app -n default --replicas=2
# (new pods pull a clean image from ECR)
```

**Option C — Delete and recreate the pod:**
```bash
kubectl delete pod <pod-name> -n default
# Kubernetes creates a replacement pod automatically
```

---

## Phase 5 — Recovery

After eradicating the threat and verifying clean pods are running:

**Release quarantine:**
```bash
kubectl label pod <pod-name> -n default guardops.io/quarantined-   # remove label
```

**Restore normal Service selector:**
```bash
kubectl patch pod <pod-name> -n default \
  -p '{"metadata":{"labels":{"app.kubernetes.io/instance": "guardops-app"}}}'
```

**Verify health:**
```bash
curl -I https://guardops.dev/healthz
guardops status --env prod
guardops runtime-status --env prod --window 15m   # should be clean
guardops sync-status --env prod                   # ArgoCD should be Synced + Healthy
```

---

## Phase 6 — Post-Incident

- [ ] File a GitHub issue with `incident` label
- [ ] Document: timeline, root cause, blast radius, remediation
- [ ] Update Falco rules if a new detection pattern is needed: `k8s/falco/custom_rules.yaml`
- [ ] Run a new deploy to cycle all pods to a known-clean image:
  ```bash
  git commit --allow-empty -m "chore: cycle pods post-incident"
  git push origin main
  ```
- [ ] Review Grafana dashboard for the incident window
- [ ] Check if the Alertmanager webhook fired correctly — if not, investigate Phase 8 setup

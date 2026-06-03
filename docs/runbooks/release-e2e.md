# Runbook: Release end-to-end verification

Run this **once per release** (before tagging `vX.Y.Z`). It exercises the full cloud
pipeline that CI cannot run hermetically. Budget ~45 min. Tear the cluster down after.

Prerequisites: AWS creds, `guardops` installed, a domain in `enable_dns_tls` mode,
`GUARDOPS_S3_BUCKET` set. Everything else is brought up by `morning-start.ps1`.

## 0. Bring the cluster up

```powershell
.\scripts\morning-start.ps1
```

Confirm: all pods Ready, `app.guardops.live` resolves, dashboard pod healthy.

## 1. Full deploy pipeline (prod)

```bash
guardops deploy --env prod --gitops
```

Expect, in order:
- [ ] Docker build succeeds (non-root image)
- [ ] SAST runs (Semgrep + Bandit + Trivy fs + Trivy image); HIGH+ would block
- [ ] ECR push + cosign keyless signature + SBOM attestation
- [ ] Helm deploy to EKS (atomic)
- [ ] ZAP DAST against the live URL; CRITICAL would auto-rollback
- [ ] GitOps: `values-override-prod.yaml` committed, ArgoCD sync triggered

## 2. Supply chain

```bash
guardops verify-image <ecr-ref>@sha256:<digest> --attestation
guardops sbom <ecr-ref>
```
- [ ] Signature verifies against the GitHub OIDC identity (Rekor)
- [ ] SBOM lists packages (CycloneDX + SPDX)

## 3. Runtime + self-healing

```bash
guardops runtime-status --since 1h
guardops quarantine-status -A
```
- [ ] Falco alerts read from Loki (the simulator fires some)
- [ ] If a CRITICAL pod alert fired, the NetworkPolicy quarantine is listed

## 4. GitOps reconciliation

```bash
guardops sync-status --env prod --wait
```
- [ ] ArgoCD reaches **Synced + Healthy** before the 300s timeout

## 5. Dashboard — live

- [ ] `https://app.guardops.live/healthz` returns ok
- [ ] `https://dashboard.guardops.live` loads, Navbar badge = **CONNECTED**
- [ ] Findings / Runs / Trends / Runtime / Metrics panels render live data

## 6. Dashboard — offline fallback (the v1.0.0 core proof)

```bash
guardops dashboard snapshot --to-s3            # publish current state
```
Then **simulate the nightly teardown** without destroying everything: scale the
dashboard deployment to 0 (or just run step 7), and from **a second device** (phone
on cellular, or another laptop) load `https://dashboard.guardops.live`.

- [ ] SPA still loads and renders the snapshot data
- [ ] Amber banner: **"Live backend offline — showing cached data from <time>"**
- [ ] Navbar badge = **SNAPSHOT** (amber), not CONNECTED
- [ ] No internal hostnames (EKS endpoint, `*.svc.cluster.local`) appear anywhere in
      the served `snapshot.json` (open it directly and grep)

Bring the backend back (scale up / re-deploy) and reload:
- [ ] Banner clears, badge returns to CONNECTED, data goes live

## 7. Tear down

```powershell
.\scripts\night-shutdown.ps1
```
- [ ] Step 0 publishes a final snapshot to S3 before destroy
- [ ] `terraform destroy` completes clean (no leftover ENIs/ALBs)
- [ ] After teardown, `dashboard.guardops.live` from any device still shows the
      snapshot with the offline banner — **this is the headline fix working**

## Sign-off

- [ ] All boxes above checked
- [ ] `pytest` + `cd web && npm test && npm run build` green on the release commit
- [ ] `pip-audit` / `npm audit` reviewed (residual = documented dev-only)
- [ ] Version bumped + changelog entry written

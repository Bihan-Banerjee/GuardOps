# Runbook: Release end-to-end verification

This runbook has **three tiers**. New users and contributors should start with
**Tier 1** — it validates every component and the durable dashboard panels with **no
AWS cost**. Maintainers run all three **once per release** (before tagging `vX.Y.Z`).

- **Tier 1 — Local component pass (no AWS).** ~15 min. Every CLI command + the four
  durable dashboard panels, against the local dashboard. No cloud, no spend.
- **Tier 2 — Live cloud pass (full pipeline).** ~45 min. The cloud pipeline CI can't
  run hermetically: ECR/cosign/SBOM, EKS, ZAP, ArgoCD, and the live dashboard panels.
- **Tier 3 — Offline-snapshot proof (the v1.0.0 headline fix).** ~10 min. Proves the
  public URL renders 24/7 from any device with the cluster down.

## How the dashboard gets its data (read this first)

The dashboard has two kinds of panels, and **it only shows durable data that was
written to the same store it reads** — locally `security/metadata/guardops.db`
(SQLite, enabled by default), in the cloud the **S3** store. So local scans only
appear on the cloud dashboard after `guardops db export --to-s3` (or a snapshot).

| Panel | Source | Populated by | Needs cluster? |
|---|---|---|---|
| Summary / Runs / Findings / Trends | metadata store (durable) | `guardops scan`, `guardops deploy` | No |
| Metrics (app/resources) | Prometheus (live) | running pods + Prometheus | Yes |
| Runtime alerts | Loki/Falco (live) | Falco events in Loki | Yes |
| Quarantine | kubectl (live) | self-heal NetworkPolicies | Yes |
| Sync status | ArgoCD (live) | `guardops deploy --gitops` | Yes |

**Most common gotcha:** an empty durable panel on the cloud dashboard almost always
means you scanned locally but never ran `guardops db export --to-s3` (or published a
snapshot) — the cloud dashboard reads S3, not your laptop's SQLite.

---

# Tier 1 — Local component pass (no AWS)

Run from a project directory. Validates the whole CLI and the four durable panels
against the **local** dashboard. Requires Docker + k3d for the deploy steps; the scan
and dashboard steps work without them.

## 1.0 Preflight

```bash
guardops doctor          # core tools OK, exits 0 (non-zero only if a core tool is missing)
guardops init            # creates .guardops.yaml + .env.example
```

## 1.1 Scans → populate the durable store

```bash
guardops scan            # Semgrep/Bandit/Trivy; persists one run to the metadata DB
guardops scan            # run twice so Trends has >1 point
```

## 1.2 Read the durable store directly (this is what the dashboard reads)

```bash
guardops history                     # list scan runs
guardops findings --severity HIGH    # query findings
guardops trends                      # per-day severity counts
guardops diff                        # new-vs-fixed between the last two runs
guardops db export                   # dump the store as JSON (sanity check)
```
- [ ] Each command returns the data from your two scans.

## 1.3 Local deploy pipeline (k3d)

```bash
guardops deploy --env local          # build → scan → import to k3d → Helm deploy
guardops status                      # pod health
guardops logs                        # app logs
guardops rollback --to-previous      # then re-deploy to confirm rollback works
```

## 1.4 Supply chain (local image)

```bash
guardops sbom <local-image-ref>      # CycloneDX + SPDX package list
guardops verify-image <ref>          # signature/attestation (skips gracefully if unsigned)
```

## 1.5 Local dashboard — durable panels

```bash
guardops dashboard                   # serves http://localhost:8081 (reads your SQLite)
```
In another shell:
```bash
curl localhost:8081/healthz
curl localhost:8081/api/v1/summary   # total_runs > 0
curl localhost:8081/api/v1/runs
curl localhost:8081/api/v1/findings
curl localhost:8081/api/v1/trends
```
For the full UI, run the SPA against it: `cd web && npm run dev` (point
`VITE_API_BASE` at `http://localhost:8081`).

- [ ] Summary / Runs / Findings / Trends render your scan data.
- [ ] Metrics / Runtime / Quarantine / Sync report `{"available": false}` — **correct**
      locally (no cluster). Tier 2 brings them online.

---

# Tier 2 — Live cloud pass (full pipeline)

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

Make local durable data visible to the **cloud** dashboard first (the in-cluster
dashboard reads the S3 store, not your laptop's SQLite):

```bash
guardops db export --to-s3                     # push runs/findings to the S3 store
```

- [ ] `https://app.guardops.live/healthz` returns ok
- [ ] `https://dashboard.guardops.live` loads, Navbar badge = **CONNECTED**
- [ ] All nine panels render: Summary / Runs / Findings / Trends (durable) +
      Metrics / Runtime / Quarantine / Sync (live)

---

# Tier 3 — Offline-snapshot proof (the v1.0.0 headline fix)

This proves the public URL renders 24/7 from any device with the cluster down.

## 6. Dashboard — offline fallback

```bash
guardops dashboard snapshot --to-s3            # publish current full state to the public S3 key
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

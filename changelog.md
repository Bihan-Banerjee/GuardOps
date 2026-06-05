# Changelog

All notable changes are documented here.
Format: [Semantic Versioning](https://semver.org)

## [1.0.0] — 2026-06-05

Phase 14 — the first stable release: completes the web dashboard, hardens the tool for
external users, and makes the public site work 24/7 at near-zero cost. Version bumped to
1.0.0 across `cli/__init__.py`, `pyproject.toml`, `web/package.json`, and the Helm chart;
published to PyPI on the `v1.0.0` tag — see [RELEASE.md](RELEASE.md).

### Added
- **Web dashboard SPA** (`web/`, Vite + React + Three.js) on guardops.live.
- **Always-on dashboard snapshot.** `guardops dashboard snapshot [--to-s3]` publishes a
  static, path-keyed snapshot of the API; the SPA falls back to it when the live API is
  unreachable (the cluster is torn down nightly), rendering last-known data from any
  device with an offline banner. Live-source payloads are redacted before publish.
  Opt-in public S3 via Terraform `enable_public_snapshot`, scoped to `dashboard/*` only.
- **`guardops doctor`** — first-run preflight checking required tools + config.
- **`guardops admission --mode audit|enforce [--dry-run]`** — cross-platform, testable
  Kyverno policy apply (Audit default), wired into `morning-start.ps1`.
- **Cross-platform CI** — pytest on Linux/macOS/Windows, a web Vitest job, and a
  `--cov-fail-under` coverage gate.
- **Docs set** — INSTALL, QUICKSTART, CONTRIBUTING, TROUBLESHOOTING, RELEASE, TESTING,
  docs/API, docs/ARCHITECTURE, **docs/SELF_HOSTING**, and a release-e2e runbook.

### Changed
- **ArgoCD Terraform module fixed + enabled** — installs only the Helm release (no
  `kubernetes_manifest` plan-time bootstrap failure); the UI moved to an ALB Ingress
  (`k8s/argocd/ingress.yaml`) on the shared group, replacing the nginx default.
- **Falco marked experimental / simulated** on the single-node cluster; memory limit
  raised 256Mi→1Gi with shrunk eBPF buffers. `runtime-status` warns accordingly.
- **Security toggles on by default** in `.guardops.yaml` (SonarQube, OWASP ZAP DAST,
  runtime gate).
- **FastAPI 0.115 → 0.136 / Starlette 1.2** to clear the Starlette CVEs.
- Lifecycle scripts publish the snapshot (morning-start + pre-teardown), apply the
  ArgoCD ingress, drive Kyverno via `guardops admission`, and night-shutdown drains the
  argocd ALB.

### Fixed
- **CI HIGH-severity gates** — Dockerfile `apt-get upgrade` clears fixable base-image
  CVEs (Trivy); the intentional public-S3 finding is suppressed (Semgrep).
- **Dependency CVEs** — floored `urllib3>=2.7.0`, `idna>=3.15` (plus the Starlette bump).
- **Snapshot info-leak** — internal endpoints/hostnames redacted from the public snapshot.
- Runtime `pod_name` field consistency; a Windows-only CLI hint made cross-platform.
- **CI hermeticity (pusher)** — `push_to_ecr` no longer requires an AWS account-ID/STS
  lookup when `docker.registry` is configured, so CI (and any pre-configured env) pushes
  without live AWS credentials.
- **SAST gate** — fixed a workflow `run`-shell-injection (GitHub context now passed via
  env, not inline `${{ }}`); justified false positives (parameterized SQLite queries,
  the non-security dedup SHA-1 fingerprint, Kyverno's short-lived IRSA role) suppressed.
- **Container CVEs** — the demo image strips `pip`/`setuptools`/`ensurepip` whose
  *vendored* `jaraco.context`/`wheel` copies were flagged, plus `apt-get upgrade` for the
  OS layer → a clean Trivy scan (0 fixable HIGH/CRITICAL).
- **Terraform import safety** — the ECR lifecycle policy uses static `for_each` keys so
  pre-existing long-lived resources (ECR/S3/OIDC) can be imported back into state.
- **Lifecycle scripts (PowerShell 5.1)** — ArgoCD apply/login guarded against the
  `Stop`+native-stderr trap; ArgoCD login moved to a `kubectl port-forward` (no DNS wait);
  `Read-TfVar` strips inline comments; `night-shutdown` preserves the
  `dashboard.guardops.live` Vercel CNAME across teardown.
- **CI cluster gating** — `HAS_EKS_CLUSTER` is kept in sync by the lifecycle scripts so a
  push never hard-fails on a torn-down cluster.

### Tests
- **100% line coverage** enforced in CI (`--cov-fail-under=100`).

### Security
- Public snapshot redaction; dependency CVE remediation; opt-in, prefix-scoped public S3.

## [0.13.0] — 2026-06-02

Phase 13 — the last feature phase before the v1.0.0 stabilization pass (full
bug-testing, QoL, ruff/mypy). Two features; the dashboard frontend SPA is a later
deliverable (it consumes this release's API).

### Added
- **Interactive deploy chooser.** Running `guardops deploy` with no flags in a
  terminal now offers a **Default / Custom / Cancel** chooser so users don't have
  to remember the eleven deploy flags. Custom walks through env, build, scanners,
  fail-on, DAST, blue-green slot, GitOps, and replicas; it then prints the
  **equivalent command** so the flags are learnable. `-i/--interactive` forces the
  chooser; `-y/--yes`, a non-TTY stream, or CI bypass it (automation unaffected).
  New `cli/commands/_deploy_wizard.py`; `deploy_cmd` refactored so the flag path
  and wizard path share one `_execute_deploy()`.
- **Web dashboard API (`backend/dashboard/`, FastAPI).** A frontend-agnostic JSON
  API visualizing every prior phase: scan findings/runs/trends/diff/summary
  (durable), plus live observability metrics (Prometheus), runtime Falco alerts
  (Loki), pod quarantine (kubectl), and ArgoCD sync. Read-only routes under
  `/api/v1/*` with `/healthz` + `/readyz`. Live sources degrade to
  `{"available": false, "reason": ...}` when the cluster is down rather than 5xx.
- **`guardops dashboard`** — runs the API locally via uvicorn (optional `dashboard`
  extra: `pip install 'guardops[dashboard]'`). In-cluster target: `backend.dashboard.app:app`.
- **S3 export bridge for durable findings.** New read-only `S3MetadataStore`
  (`backend/metadata/s3_store.py`) hydrates an in-memory SQLite from the
  `export_json()` dump and **delegates all reads to `SqliteMetadataStore`** (no SQL
  re-implemented). `guardops db export --to-s3` publishes the dump to
  `s3://<bucket>/<prefix>/<project>/latest.json`. Survives the nightly
  `terraform destroy` at ~$0 (no always-on DB). `get_store` gains an `s3` backend.
- **Shared-credential auth** (`backend/dashboard/auth.py`): bearer token or HTTP
  Basic, from env/K8s Secret; enforced only when a credential is configured.
- **Deployment artifacts:** `Dockerfile.dashboard`, `k8s/dashboard/`
  (ConfigMap, Secret template, Deployment + minimal RBAC, Service, TLS Ingress for
  `app.guardops.live`), an `app.guardops.live` Route53 record in `modules/dns-tls`,
  and `scripts/setup-dashboard.ps1` (build → push → apply).
- **Lifecycle integration:** `morning-start.ps1` now builds/pushes `dashboard-latest`
  (new `Ensure-DashboardImage`), deploys the dashboard, auto-discovers the reports
  bucket, generates an auth password, opens the `:8081` port-forward, and prints the
  URL + credentials in the summary. `night-shutdown.ps1` removes the dashboard's
  non-Helm objects (the Ingress is already drained with the rest in Step 1). The
  prod app Ingress and the dashboard Ingress share **one ALB** via the
  `alb.ingress.kubernetes.io/group.name: guardops` IngressGroup, so the single
  `alb_dns_name` alias covers both `guardops.live` and `app.guardops.live` (the
  wildcard `*.guardops.live` ACM cert already covers the subdomain).
- Config: `metadata.s3_*` and a `dashboard` section in `DEFAULT_CONFIG`; `dashboard`
  optional-dependency group in `pyproject.toml`; dashboard test deps in `requirements-dev.txt`.
- **Frontend integration (`web/` Vite/React SPA on Vercel).** Verified the SPA↔API
  contract (all `/api/v1` paths, auth header, response shapes). Backend now enables
  **CORS** for the SPA origin (default `https://dashboard.guardops.live` + apex/www +
  Vite dev ports; `GUARDOPS_DASHBOARD_CORS_ORIGINS` / `cors_origin_regex` overrides),
  with preflight handled ahead of auth. The SPA is served from `dashboard.guardops.live`
  via a Route53 CNAME to Vercel (`modules/dns-tls`) — apex stays on the ALB, registrar
  nameservers unchanged. Fixed a field mismatch in the runtime feed (`pod_name`).
- ~45 new tests: deploy wizard (gate truth-table, custom/cancel, equivalent command),
  S3 store round-trip + filters + read-only, `db export --to-s3`, the dashboard
  API + auth + CORS via FastAPI `TestClient`.

### Changed
- `cli/commands/deploy_cmd.py`: deploy body extracted to `_execute_deploy(opts, config)`;
  added `--interactive/-i` and `--yes/-y`. No change to existing flag behavior.

## [0.12.0] — 2026-06-02

### Added
- **Scan metadata database (Phase 12): scans now have a memory.** Every
  `guardops scan` and the scan step of `guardops deploy` is persisted to a local
  SQLite database (stdlib `sqlite3`, zero new dependencies) instead of being
  written once to `security/reports/` and forgotten.
- `backend/metadata/`: a `MetadataStore` abstraction with a SQLite implementation
  (`scan_runs`, `findings`, `tool_runs` tables, schema auto-created on first use)
  so a networked backend (Postgres) can be added for the v1.0.0 dashboard without
  changing any command. `persist_report_safe()` records a report and is strictly
  non-fatal — a DB error only logs a warning, never breaks a scan or deploy.
- CLI query/analysis actions:
  - `guardops history` — list past scan runs (project/env/image filters, `--json-output`).
  - `guardops findings` — query stored findings by severity threshold, tool, CVE, or image.
  - `guardops trends` — per-day severity counts over time.
  - `guardops diff` — NEW vs FIXED findings between two runs; exits 1 when a new
    CRITICAL/HIGH is introduced (a CI regression gate).
  - `guardops db` — `init`, `prune` (`--keep-days`/`--keep-last` or the config
    retention policy), and `export` (full DB → JSON, the bridge for the dashboard).
- `metadata` section in `.guardops.yaml` (`enabled`, `backend`, `path`,
  `retention_days`, `retention_keep_last`) with safe defaults; helpers
  `is_metadata_enabled()` / `resolve_metadata_db_path()` in `cli/utils/config.py`.
- ~46 new tests: store round-trip + filters + prune + non-fatal guarantee, plus
  the five commands via Click's `CliRunner`.

### Changed
- `cli/commands/scan_cmd.py` and `deploy_cmd.py` persist each report after it is
  generated (deploy records environment + git SHA, and records blocked runs too).
- `.gitignore`: ignore `security/metadata/` (the SQLite DB is never committed).

## [0.11.0] — 2026-06-01

### Added
- **Supply chain security (Phase 11): SBOM + Cosign keyless signing + Kyverno.**
- CI `container-scan`: generate a Syft SBOM (CycloneDX + SPDX), upload it as an
  artifact and to S3, sign the image **by digest** with cosign keyless (GitHub
  OIDC → Fulcio → Rekor), and attest the SBOMs + SLSA-style provenance. No new
  secrets — keyless reuses the job's `id-token: write`.
- `infra/terraform/modules/kyverno`: Kyverno admission controller (Helm) plus an
  IRSA role that lets its controllers read cosign signatures from private ECR.
  Gated by `enable_kyverno`; `kyverno_policy_action` selects Audit vs Enforce.
- `modules/eks`: registered the cluster IRSA OIDC provider + `oidc_provider_*`
  outputs (prerequisite for SA-scoped IAM roles).
- `k8s/kyverno/`: ClusterPolicies — keyless image-signature verification
  (`mutateDigest`), a required CycloneDX SBOM attestation, and a best-practice
  pack (no `:latest`, ECR-only, runAsNonRoot, drop ALL caps, no privilege
  escalation / privileged / host namespaces, resource requests+limits; read-only
  rootfs as an Audit-only advisory).
- `scripts/setup-admission-control.ps1`: apply the policies, with `-Enforce` to
  flip Audit → Enforce (and the verify webhooks to `failurePolicy: Fail`).
- CLI: `guardops sbom <image>` (Syft) and `guardops verify-image <ref>` (cosign
  verify; `--attestation` also checks the SBOM), backed by
  `backend/security/sbom_runner.py` + `cosign_verifier.py`.
- `docs/runbooks/supply-chain-admission-control.md`: install, verify, Audit →
  Enforce, rollback, and troubleshooting.

### Changed
- `modules/ecr`: cosign-aware lifecycle (expire untagged after 7 days, keep last
  25 tagged) so image signatures are not expired out from under running images;
  the CI repo-create step applies the same policy to the `guardops-app` repo.
- `morning-start.ps1`: new Phase 11 step applies the Kyverno ClusterPolicies once
  Kyverno is Ready (gated by `enable_kyverno`); step counter is now `/12`.
- `night-shutdown.ps1`: delete the GuardOps ClusterPolicies and uninstall Kyverno
  first (clears admission webhooks), and detach `module.kyverno` from state before
  `terraform destroy`.
- Helm chart bumped 0.4.0 → 0.5.0.

### Fixed
- CI auth: preserve the GitHub Actions OIDC provider + CI role across the nightly
  `terraform destroy` (`night-shutdown.ps1` detaches them from state, like the
  Route53 zone; `morning-start.ps1` re-imports them via `Import-GithubOidc`) so
  CI no longer fails with "No OpenIDConnect provider found … for
  https://token.actions.githubusercontent.com" while the cluster is down.
- mypy: annotate the ArgoCD sync payload in `backend/pipeline/gitops_writer.py`
  as `dict[str, Any]` so `requests.post(json=…)` type-checks.
- Removed the legacy static CI IAM user (`aws_iam_user.ci` + inline policy +
  access key + `ci_user_*` outputs) from `modules/iam`. It was superseded by
  GitHub OIDC in Phase 6 and was unused; its orphaned presence in AWS caused a
  `409 EntityAlreadyExists` that aborted `morning-start.ps1`. Removing it also
  deletes unused long-lived credentials. (Delete the old AWS user once — see the
  note in `modules/iam/main.tf`.)
- `morning-start.ps1`: warm the helm chart repository cache (`Ensure-HelmRepos`)
  before the full `terraform apply`. The Terraform helm provider downloads charts
  through the shared helm CLI cache under `%TEMP%\helm`; on a fresh/cleared TEMP
  that cache is empty and the apply failed with "could not download chart: no
  cached repo found (try 'helm repo update')". Adds every repo the modules pull
  (eks-charts, jetstack, argo, falcosecurity, grafana, kyverno,
  prometheus-community) and runs `helm repo update`.
- Kyverno `verify-images` policy: digest pinning is now action-dependent
  (`__DIGEST_PIN__`). Kyverno rejects `mutateDigest: true` under
  `validationFailureAction: Audit` ("mutateDigest must be set to false for 'Audit'
  failure action"), so `mutateDigest`/`verifyDigest` are `false` in Audit and
  `true` only in Enforce. The setup/morning-start scripts substitute it.
- `morning-start.ps1`: auto-retry the full `terraform apply` once (transient
  helm/webhook rollout races on a busy single node), and adopt an orphaned
  `kyverno` helm release into state (`Import-KyvernoRelease`) to avoid
  "cannot re-use a name that is still in use".
- Kyverno chart pinned **3.2.6 → 3.4.6** (Kyverno 1.12 → 1.14.5). Chart 3.2.x
  pulled its report-cleanup CronJobs *and* helm hooks (`policyReportsCleanup`,
  `remove-configmap`) from `bitnami/kubectl:1.28.5`, which was removed from Docker
  Hub (Bitnami image purge) → ImagePullBackOff blocked both the `wait=true` install
  and the uninstall hooks. 3.4.x pulls those images from `reg.kyverno.io` /
  `alpine/kubectl`, fixing it at the source. The `policyReportsCleanup` post-install
  hook is still disabled (blocking + pointless on a nightly cluster). IRSA
  serviceAccount annotations and the other value keys were verified against 3.4.6.
- `morning-start.ps1`: the new import/repo helpers now use the repo's
  `try { … 2>&1 | Out-Null } catch { }` pattern so a not-in-state `terraform
  state show` no longer becomes a terminating error under `ErrorAction Stop`.
- `night-shutdown.ps1`: preserve the ECR repos + S3 reports bucket by detaching
  them from state before `terraform destroy` (like the Route53 zone). Both are
  non-empty (more so now CI writes SBOMs to S3 + signatures to ECR), so `destroy`
  was erroring with `BucketNotEmpty` / `RepositoryNotEmpty` and aborting the
  teardown before its final steps. `force_destroy`/`force_delete` stay false so
  data is never deleted; `morning-start.ps1` re-adopts the repo + bucket on the
  next apply (only those two 409 on create — the S3 sub-resources and ECR
  lifecycle policy are idempotent config applies).

### Notes
- Policies ship in **Audit** by default — verify the PolicyReports, then flip to
  Enforce. Read-only root filesystem stays Audit-only until the app chart adds a
  writable `emptyDir` (tracked follow-up).

## [0.10.2] — 2026-05-31

### Changed
- Default domain switched from the `guardops.dev` placeholder to `guardops.live`
- App Ingress now terminates HTTPS at the ALB via an **AWS ACM** certificate
  (cert-manager/Let's Encrypt cannot supply a certificate to an ALB)
- `terraform.tfvars.example`: added the Phase 10 DNS/TLS/ArgoCD block and the
  required `github_repo` variable

### Fixed
- CI: install the `guardops` package in the runtime-gate job so
  `guardops runtime-status` resolves on PATH
- `setup-runtime-security.ps1`: scope the enabled-flag replace to the
  `runtime_security` block (it was also flipping `self_healing`)
- Helm: base `values.yaml` ingress class is `nginx` for local k3d; pod
  `runAsUser`/`fsGroup` aligned to the image's non-root UID 10001
- `dns-tls`: install cert-manager only after the AWS Load Balancer Controller is
  ready (avoids the "no endpoints available" webhook race)
- `morning-start.ps1`: certificate status check no longer crashes on a fresh
  cluster with zero certificates; re-imports the preserved Route53 zone on startup
- `night-shutdown.ps1`: survive `kubectl` "No resources found"; preserve the
  Route53 zone and detach cluster-resident (helm/k8s) resources before destroy so
  the teardown completes cleanly
- `iam_oidc`: drop `prevent_destroy` so the nightly `terraform destroy` is not
  blocked (the role ARN is stable by name, so `AWS_ROLE_ARN` is unaffected)
- Packaging: fix `authors` metadata so the PyPI author renders, correct the
  changelog URL, and drop the unused `kubernetes` dependency

### Removed
- Stray committed sdist directories (`guardops-0.4.0/`, `guardops-0.8.0/`)

## [0.2.0] — 2026-04-30

### Added
- `guardops scan` command with Semgrep, Bandit, Trivy, SonarQube
- HTML + JSON security reports saved to `security/reports/`
- Unified severity scale (CRITICAL/HIGH/MEDIUM/LOW) across all tools
- Deployment blocked on HIGH+ findings by default
- GitHub Actions 5-job CI/CD pipeline with security gates
- AWS ECR push support via `guardops deploy --push`
- 154 unit tests covering all security runners and deployer

### Fixed
- Windows cp1252 encoding error when piping YAML to kubectl
- Trivy `Vulnerabilities: null` crash on clean images
- SonarQube polling timeout handled gracefully

## [0.1.0] — 2026-03-15

### Added
- `guardops init` setup wizard
- `guardops deploy` — Docker build + k3d deploy
- `guardops status` — pod health table
- `guardops logs` — live log streaming
- Local Kubernetes via k3d with automatic image import
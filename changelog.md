# Changelog

All notable changes are documented here.
Format: [Semantic Versioning](https://semver.org)

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
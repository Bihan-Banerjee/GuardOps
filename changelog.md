# Changelog

All notable changes are documented here.
Format: [Semantic Versioning](https://semver.org)

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
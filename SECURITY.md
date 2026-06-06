# Security Policy

GuardOps is a DevSecOps tool, so we hold its own security to a high bar. This document
explains how to report vulnerabilities and the security properties the project enforces.

## Supported versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | ✅ Yes    |
| < 1.0   | ❌ No (pre-release) |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Instead, report privately via one of:

- **GitHub Security Advisories** — open a draft advisory at
  `Security → Advisories → Report a vulnerability` on this repository (preferred).
- **Email** — send details to bihanbanerjee26@gmail.com.

Please include:

- A description of the issue and its impact.
- Steps to reproduce (a proof-of-concept if possible).
- Affected version(s) and environment.

**Response targets (best-effort for a solo-maintained project):**

- Acknowledgement: within **3 business days**.
- Initial assessment: within **7 business days**.
- Fix or mitigation plan: communicated after triage.

We follow **coordinated disclosure**: please give us a reasonable window to release a
fix before any public disclosure. We are happy to credit reporters in the release notes.

## Security properties of GuardOps itself

The project applies its own gates in CI (`.github/workflows/ci.yaml`):

- **SAST** — Semgrep + Bandit scan the source on every push; the pipeline blocks on
  HIGH/MEDIUM findings (with a documented, justified suppression allowlist).
- **Container scanning** — Trivy scans the built image; the pipeline blocks on fixable
  HIGH/CRITICAL CVEs. The release image carries **0 fixable HIGH/CRITICAL** CVEs.
- **Supply chain** — release images are **signed with Cosign (keyless, via GitHub OIDC,
  recorded in the Rekor transparency log)** and ship a **Syft SBOM** (CycloneDX + SPDX)
  attested to the image. Kyverno admission policies reject unsigned images in-cluster.
- **No static cloud credentials** — CI authenticates to AWS via **GitHub OIDC**
  (short-lived role assumption); there are no long-lived `AWS_ACCESS_KEY_ID` secrets.
- **Least privilege containers** — multi-stage builds with non-root users, dropped
  capabilities, and no build tools in the runtime image.
- **Dependency hygiene** — runtime dependencies are pinned/floored to patched versions;
  `pip-audit` / `npm audit` are reviewed each release (residual findings are limited to
  dev-only toolchain packages and are documented).

## Handling secrets

- Never commit credentials. Secrets are provided via environment variables / CI secrets
  (e.g. `AWS_ROLE_ARN`, `PYPI_API_TOKEN`, `SONAR_TOKEN`), never hard-coded.
- `.tfstate`, `.env`, kubeconfigs, and local databases are git-ignored.
- The public dashboard snapshot is **redacted** (`_redact_live`) to strip internal
  endpoints and hostnames before publishing.

## Scope

This policy covers the GuardOps CLI, backend libraries, dashboard, and the Terraform/
Kubernetes definitions in this repository. It does **not** cover vulnerabilities in
third-party tools GuardOps invokes (report those to their respective projects) or in a
user's own cloud configuration.

Thank you for helping keep GuardOps and its users safe.

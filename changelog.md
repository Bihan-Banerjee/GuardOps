# Changelog

All notable changes are documented here.
Format: [Semantic Versioning](https://semver.org)

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
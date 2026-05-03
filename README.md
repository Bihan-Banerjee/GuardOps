# GuardOps

> Autonomous DevSecOps CLI — build, scan, and deploy with security gates at every stage.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

GuardOps automates the entire secure software delivery lifecycle.
Given any application repo, it builds your Docker image, runs
comprehensive security scans, and deploys to Kubernetes — blocking
the pipeline if HIGH or CRITICAL vulnerabilities are found.

## Install

```bash
pip install guardops
```

## Quick Start

```bash
# Set up a new project
guardops init

# Build, scan, and deploy
guardops deploy

# View pod health
guardops status

# Run security scans only
guardops scan
```

## Security Tools

| Tool | Type | Catches |
|------|------|---------|
| Semgrep | SAST | Code patterns, secrets, OWASP Top 10 |
| Bandit | SAST | Python-specific vulnerabilities |
| Trivy | Container | CVEs in OS packages and dependencies |
| SonarQube | Quality | Security hotspots, code smells |

## Versioning

| Version | Status | Description |
|---------|--------|-------------|
| v0.2.1 | Current | CLI + local Kubernetes + security scanning |
| v0.3.x | In development | Helm charts + AWS EKS + rollback |
| v1.0.0 | Planned | Full production release |

## Requirements

- Python 3.11+
- Docker Desktop
- kubectl
- k3d (local) or AWS EKS (production)
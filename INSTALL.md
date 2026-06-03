# Installing GuardOps

## From PyPI (recommended)

```bash
pip install guardops

# optional — only needed to run the web dashboard API (`guardops dashboard`)
pip install 'guardops[dashboard]'
```

Then verify your machine:

```bash
guardops doctor
```

`doctor` checks every external tool GuardOps shells out to and your project config,
reporting everything missing at once with install hints.

## From source (for development)

```bash
git clone https://github.com/Bihan-Banerjee/GuardOps
cd GuardOps
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"                          # editable + dev + dashboard deps
guardops --version
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the test/lint workflow.

## Prerequisites

| Tool | Needed for | Install |
|------|-----------|---------|
| Python 3.11+ | the CLI itself | https://python.org |
| Docker | build + run images | https://docs.docker.com/get-docker/ |
| kubectl | talk to the cluster | https://kubernetes.io/docs/tasks/tools/ |
| Helm 3.x | deploy the app | https://helm.sh/docs/intro/install/ |
| k3d | local deploys (`--env local`) | https://k3d.io |
| Semgrep, Bandit, Trivy | the pre-deploy security gate | `pip install semgrep bandit`, https://trivy.dev |
| cosign, syft | image signing + SBOM (prod) | https://docs.sigstore.dev, https://github.com/anchore/syft |
| AWS CLI, Terraform | provisioning EKS/ECR/S3 (prod) | https://aws.amazon.com/cli/, https://terraform.io |

You only need the cloud/supply-chain tools for `--env prod`. For a local k3d loop,
Docker + kubectl + Helm + k3d + the scanners are enough. `guardops doctor` tells you
exactly which you're missing.

## Platform notes

- **Linux/macOS:** the CLI is pure Python and runs natively. The lifecycle automation
  (`scripts/*.ps1`) is PowerShell — install **PowerShell Core (`pwsh`)** to run it, or
  drive `guardops` directly.
- **Windows:** everything runs natively in PowerShell. Output is UTF-8 (the CLI
  reconfigures legacy cp1252 consoles automatically).

## Next steps

- [QUICKSTART.md](QUICKSTART.md) — deploy your first app
- [docs/SELF_HOSTING.md](docs/SELF_HOSTING.md) — host the dashboard yourself, cheaply, without a cluster
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — if something doesn't work

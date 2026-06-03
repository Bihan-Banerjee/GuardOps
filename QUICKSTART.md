# Quickstart — deploy your first app

This walks you from zero to a scanned, deployed app on a local k3d cluster in about
five minutes. No AWS required.

## 1. Check your machine

```bash
pip install guardops
guardops doctor
```

Install anything `doctor` flags as **required** (docker, kubectl, helm) plus a local
k3d cluster.

## 2. Try it against the bundled demo app

The repo ships a tiny Flask app at [test-project/](test-project/) — a Dockerfile, an
`app.py`, and a ready-made `.guardops.yaml`. From a clone:

```bash
cd test-project
guardops deploy            # build → scan → deploy to local k3d
guardops status            # pod health
```

What just happened (the same `guardops deploy` pipeline used in prod):

1. **Build** a multi-stage, non-root Docker image
2. **Scan** with Semgrep + Bandit + Trivy — blocks on HIGH+ findings
3. **Deploy** to k3d via Helm (atomic; auto-rollback on failure)

## 3. In your own project

```bash
cd my-app
guardops init              # scaffold .guardops.yaml (edit project/image/namespace)
guardops doctor            # confirm config is picked up
guardops deploy            # local k3d
```

## 4. Explore

```bash
guardops scan              # security scans only, no deploy
guardops logs -f           # stream pod logs
guardops findings --severity HIGH      # query stored findings (metadata DB)
guardops trends            # severity counts over time
guardops rollback          # roll back the last Helm release
guardops dashboard         # serve the web dashboard API at :8081
```

## 5. Going to production

`--env staging` and `--env prod` push to ECR and deploy to EKS, adding DAST, cosign
signing, SBOM attestation, GitOps/ArgoCD, and runtime Falco gating. That path needs
the AWS infra in [infra/terraform/](infra/terraform/) and the lifecycle scripts — see
the [README](README.md) and [docs/runbooks/](docs/runbooks/).

> The prod cluster is ephemeral (spun up/down daily to save cost). The public
> dashboard still works 24/7 via a cached snapshot — see the README's
> "Cluster lifecycle & the always-on dashboard".

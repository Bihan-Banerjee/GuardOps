# Troubleshooting

Run `guardops doctor` first — it catches most setup problems (missing tools, no config)
in one shot.

## A command says "X not found" / "not on PATH"

GuardOps shells out to docker/kubectl/helm/semgrep/bandit/trivy/cosign/syft/aws/
terraform. Install the named tool (see [INSTALL.md](INSTALL.md)) and re-run
`guardops doctor` to confirm it's now visible.

## `guardops deploy` blocks with "HIGH findings"

That's the pre-deploy gate working. Review the findings, fix or triage them, then
re-deploy. To deploy anyway during development, lower `security.fail_on_severity` in
`.guardops.yaml` or pass `--skip-scan` (dev only — never in CI).

## The dashboard loads but shows no data

This is expected when the cluster is down, and it is handled. The dashboard's **live
API (`app.guardops.live`) only runs while the EKS cluster is up** — the cluster is
destroyed nightly to save cost. The public SPA then falls back to a **static snapshot**:

- If you see an **amber "Live backend offline — showing cached data from \<time\>"**
  banner, that's the fallback working as designed. Bring the cluster up
  (`scripts/morning-start.ps1`) for live data.
- If you see **no data and no banner**, the snapshot isn't wired up. Check:
  1. `VITE_SNAPSHOT_URL` is set in the SPA build to the public snapshot URL.
  2. Terraform `enable_public_snapshot = true` (exposes only the `dashboard/*` S3
     prefix) and `terraform output dashboard_snapshot_url` matches step 1.
  3. A snapshot has been published: `guardops dashboard snapshot --to-s3 --bucket <b>`.
  4. The object is reachable: open `VITE_SNAPSHOT_URL` directly in a browser.

Locally (Vite dev server): the SPA proxies `/api` to `http://localhost:8081`, so start
the backend with `guardops dashboard` in another terminal, or set `VITE_SNAPSHOT_URL`.

## Local development: the dashboard API won't start

`guardops dashboard` needs the optional extra:

```bash
pip install 'guardops[dashboard]'
```

## `morning-start.ps1` fails on Linux/macOS

The lifecycle scripts are PowerShell. Install **PowerShell Core** and run them with
`pwsh ./scripts/morning-start.ps1`, or drive `guardops` + `terraform` + `kubectl`
directly.

## `terraform destroy` fails on subnet/ENI deletion

An ALB or ENI is still attached. `night-shutdown.ps1` deletes Ingress objects and waits
for ALB removal first — if you ran destroy manually, delete Ingresses and wait ~5 min,
then re-run destroy.

## Snapshot published but the public site is still blank from another device

- Confirm the S3 object is **publicly readable** (`enable_public_snapshot = true`) and
  has a **CORS** rule allowing your SPA origin (both are in `modules/s3`).
- The snapshot's durable data comes from the S3 metadata export — publish it with
  `guardops db export --to-s3` (CI does this) so the snapshot isn't empty.

## Windows console shows mojibake (`â„¹`, `âœ"`)

The CLI reconfigures stdout/stderr to UTF-8 automatically; if you piped output through
a tool that forces cp1252, set `PYTHONUTF8=1`.

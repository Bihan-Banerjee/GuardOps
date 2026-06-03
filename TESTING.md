# Testing GuardOps

GuardOps is tested in four layers. Default `pytest` is hermetic — it shells out to
nothing real (Docker/kubectl/helm/Semgrep/Trivy/cosign are all mocked), so it runs
anywhere in seconds. The heavier layers are opt-in.

| Layer | What it covers | How to run | In CI? |
|-------|----------------|-----------|--------|
| Unit | Each module in isolation (parsers, severity logic, store SQL, CLI option handling) | `pytest` | ✅ all 3 OSes |
| Integration (mocked) | Cross-module wiring (deploy→build→scan→push→deploy; dashboard app→sources→store) with external tools stubbed | `pytest` | ✅ |
| Integration (k3d) | Real `deploy --env local → status → rollback → switch` against a local k3d cluster | `RUN_K3D=1 pytest -m k3d` | opt-in |
| Frontend | SPA live→snapshot fallback + offline state (Vitest), production build | `cd web && npm test && npm run build` | ✅ |
| Manual e2e | Full cloud pipeline against EKS once per release | [`docs/runbooks/release-e2e.md`](docs/runbooks/release-e2e.md) | manual |

## Running the suites

```bash
# Python — hermetic unit + mocked-integration (default)
pip install -r requirements-dev.txt
pytest                                   # quiet, fast
pytest --cov=cli --cov=backend           # with coverage
pytest --cov=cli --cov=backend --cov-fail-under=55   # the CI gate

# Frontend
cd web
npm ci
npm test                                 # Vitest (jsdom)
npm run build                            # production bundle

# Lint + types (CI gate, Linux)
ruff check cli/ backend/
mypy cli/ backend/ --ignore-missing-imports
```

## Coverage gate

CI enforces `--cov-fail-under=55` on `cli/` + `backend/` (current total ≈ 56%). The
gate is a **ratchet**: raise the floor whenever coverage climbs, never lower it. The
thinnest-covered areas are the external-tool wrappers (`backend/pipeline/*`,
`backend/security/*_runner.py`, `cli/utils/system.py`) — prioritise those when
adding tests.

## CLI permutation matrix

Each `guardops` command is exercised with `click.testing.CliRunner` and mocked
collaborators. The combinations below are the equivalence classes worth covering —
not every literal product (e.g. `--slot` is meaningless for `--env local`).

| Command | Axes (equivalence classes) | Key assertions |
|---------|----------------------------|----------------|
| `deploy` | `--env {local,staging,prod}` × `--slot {none,blue,green}` × `--gitops {on,off}` × `--skip-scan {on,off}` | ECR push only for staging/prod; DAST only for prod; GitOps writes `values-override-<env>.yaml` only when `--gitops`; HIGH+ findings block unless `--skip-scan`; release name includes env+slot suffix |
| `scan` | findings present / absent / tool-error | exit non-zero on HIGH+; report persisted |
| `rollback` | `--revision N` / default | calls `helm rollback` with the right revision |
| `switch` | `--slot {blue,green}` | patches Service selector to the slot |
| `runtime-status` | `--since {1h,24h}` × `--fail-on {LOW..CRITICAL}` | exit non-zero only at/above threshold; Loki-unreachable degrades, not crashes |
| `quarantine-status` | namespace / `-A` / release filter | kubectl JSON parsed; missing kubectl → friendly message |
| `sync-status` | `--env {staging,prod}` × `--wait` | polls until Synced+Healthy or times out |
| `verify-image` | valid / unsigned / `--attestation` | exit reflects cosign result |
| `findings` | `--severity` × `--tool` × `--cve` (and combinations) | filters compose; severity is "at or above" |
| `trends` / `diff` | empty store / one run / two runs | empty store renders, not errors; diff marks new blocking |
| `db export` | stdout / `--output` / `--to-s3` | S3 key = `<prefix>/<project>/latest.json` |
| `dashboard` | no subcommand (server) / missing extra | hands `backend.dashboard.app:app` to uvicorn; friendly hint when extra absent |
| `dashboard snapshot` | `--out` / `--to-s3` / both / neither | envelope keyed by API path; live sources redacted; S3 key `dashboard/snapshot.json` |

## Conventions

- Shared factories live in [`tests/conftest.py`](tests/conftest.py): `make_finding`,
  `make_report`, and the `runner` (CliRunner) fixture. Build a scan in two lines, not
  by hand-constructing dataclasses.
- Mock at the boundary (the `subprocess`/`requests`/`boto3` call), not the function
  under test, so the test still exercises real branching logic.
- A store-write must never raise — assert the non-fatal `PersistResult`, not an
  exception.
- New frontend logic that touches `api.js`/data fetching gets a Vitest test under
  `web/src/__tests__/`.

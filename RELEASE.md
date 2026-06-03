# Releasing GuardOps

GuardOps follows [Semantic Versioning](https://semver.org). The version lives in **one**
place — `cli/__init__.py` (`__version__`) — and is imported everywhere (CLI
`--version`, the dashboard API `version` field, the SPA via `/api/v1/meta`).

## Cutting a release

1. **Bump the version** in lockstep:
   - `cli/__init__.py` — `__version__` (source of truth)
   - `pyproject.toml` — `[project].version`
   - `web/package.json` — `version`
   - `k8s/helm/guardops-app/Chart.yaml` — `version` / `appVersion`
2. **Update `changelog.md`** — add a dated section (Added / Changed / Fixed).
3. **Green checks** — `ruff`, `mypy`, `pytest --cov-fail-under=…` on Linux/macOS/Windows,
   plus `cd web && npm test && npm run build`. CI runs all of these.
4. **Security sign-off** — `pip-audit` and `npm audit` reviewed; residual risk documented.
5. **Manual e2e** — run [docs/runbooks/release-e2e.md](docs/runbooks/release-e2e.md) once
   against EKS.
6. **Tag + GitHub Release** — create a release with tag `vX.Y.Z`. This triggers
   `.github/workflows/publish.yaml`, which builds the wheel, **verifies the tag matches
   the package version**, and publishes to PyPI (`PYPI_API_TOKEN`).
7. **Verify** — `pip install guardops==X.Y.Z` in a fresh venv; `guardops --version`.

## Branching

Work on feature branches off `main`; PRs must pass CI. `main` is always releasable.

## Versioning rules

- **PATCH** — bug fixes, no API/CLI change.
- **MINOR** — new commands/flags/endpoints, backward compatible.
- **MAJOR** — breaking CLI flags, config schema, or API changes.

The first stable release is **v1.0.0** (Phase 14): the always-on dashboard snapshot,
the cross-platform test matrix, `guardops doctor`, and the full docs set.

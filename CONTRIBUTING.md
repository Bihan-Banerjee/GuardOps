# Contributing to GuardOps

Thanks for helping! GuardOps is a Python CLI + FastAPI dashboard backend + React SPA +
Terraform/Helm infra. This guide covers the dev loop.

## Setup

```bash
git clone https://github.com/Bihan-Banerjee/GuardOps
cd GuardOps
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# frontend (only if you touch web/)
cd web && npm ci && cd ..
```

## The checks CI runs (run them before pushing)

```bash
ruff check cli/ backend/
mypy cli/ backend/ --ignore-missing-imports
pytest --cov=cli --cov=backend --cov-fail-under=55

# frontend
cd web && npm test && npm run build
```

All four must pass. The Python suite runs on Linux, macOS, and Windows in CI, so avoid
OS-specific assumptions (paths, shells). See [TESTING.md](TESTING.md) for the full test
layering and the CLI permutation matrix.

## Conventions

- **Style:** match the surrounding code. `ruff` (line length 100) and `mypy` are
  enforced. Keep the existing comment density and the "why, not what" docstring style.
- **Output:** never `print()`. Use the helpers in `cli/utils/output.py`
  (`success`/`info`/`warn`/`error`/`header`/`section`).
- **Subprocess:** go through `cli/utils/system.py` (`run_command`), never raw
  `subprocess` with `shell=True`. Pass command lists, never strings.
- **Storage:** depend on `MetadataStore` (`backend/metadata/base.py`), never `sqlite3`
  directly. A store write must never raise — return a non-fatal result.
- **Tests:** new behavior gets a test. Use the `make_finding` / `make_report` / `runner`
  fixtures in `tests/conftest.py`. Frontend logic gets a Vitest test in
  `web/src/__tests__/`.

## Pull requests

1. Branch off `main`.
2. Keep the change focused; update docs + `changelog.md` when behavior changes.
3. Ensure the four checks above pass locally.
4. PR description: what changed and why. Link any issue.

Commit messages: imperative mood ("Add snapshot fallback", not "Added").

## Security

Found a vulnerability? Please open a private report via the
[Bug Tracker](https://github.com/Bihan-Banerjee/GuardOps/issues) rather than a public PR.
Run `/security-review` (or `pip-audit` + `npm audit`) on your branch before submitting.

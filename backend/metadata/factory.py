"""
backend/metadata/factory.py

The only entry point command code should use to reach the metadata store.

- get_store(config)        → pick a MetadataStore from config["metadata"]["backend"]
- resolve_db_path(config)  → absolute path of the SQLite file (relative → under cwd)
- persist_report_safe(...) → the non-fatal write helper called by scan/deploy

persist_report_safe is the contract that keeps Phase 12 invisible when it fails: a
locked, missing, or misconfigured database prints a single warning and the
scan/deploy continues. It NEVER raises and NEVER calls sys.exit.
"""

from pathlib import Path
from typing import Optional

from cli.utils.output import info, warn

from backend.metadata.base import MetadataStore, NullMetadataStore, PersistResult
from backend.metadata.sqlite_store import SqliteMetadataStore

DEFAULT_DB_PATH = "security/metadata/guardops.db"


def resolve_db_path(config: dict) -> str:
    """Absolute path to the SQLite DB. Relative paths resolve under the project
    root (cwd), mirroring cli.utils.config.get_config_path()."""
    raw = (config.get("metadata", {}) or {}).get("path") or DEFAULT_DB_PATH
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return str(path)


def get_store(config: dict) -> MetadataStore:
    """Return the configured MetadataStore.

    'sqlite' (default) is the local CLI/CI backend. 's3' is the read-only durable
    backend the dashboard uses (Phase 13) — it reads the export object published by
    `guardops db export --to-s3`. Any other backend yields a NullMetadataStore so
    callers stay non-fatal."""
    meta = config.get("metadata", {}) or {}
    backend = (meta.get("backend") or "sqlite").lower()
    if backend == "sqlite":
        return SqliteMetadataStore(resolve_db_path(config))
    if backend == "s3":
        # Lazy import: only the dashboard sets backend=s3, so the CLI never pays for it.
        from backend.metadata.s3_store import S3MetadataStore
        try:
            return S3MetadataStore.from_config(config)
        except Exception as e:  # e.g. no bucket configured — stay non-fatal
            return NullMetadataStore(f"s3 metadata backend unavailable: {e}")
    return NullMetadataStore(
        f"metadata backend '{backend}' is not supported (use 'sqlite' or 's3')"
    )


def persist_report_safe(
    report,
    config: dict,
    *,
    environment: str = "",
    git_sha: str = "",
    source: str = "scan",
) -> Optional[PersistResult]:
    """
    Persist a ConsolidatedReport without ever interrupting the caller.

    Returns the PersistResult on a real attempt, or None when persistence is
    disabled. Any failure is surfaced as a warning only — the scan/deploy that
    called this proceeds regardless.
    """
    meta = config.get("metadata", {}) or {}
    if not meta.get("enabled", True):
        return None

    # The persist itself must not raise (the store swallows DB errors), but wrap it
    # anyway so resolving the store can't either.
    try:
        result = get_store(config).persist_report(
            report, environment=environment, git_sha=git_sha, source=source
        )
    except Exception as e:  # belt-and-suspenders: never break a scan/deploy
        _emit(warn, f"Metadata DB unavailable (continuing): {e}")
        return None

    # Messaging is guarded separately: even console output can raise (e.g. a
    # legacy non-UTF-8 Windows terminal choking on a glyph), and a status line
    # must never be what turns a successful scan into a crash.
    if result.success:
        _emit(
            info,
            f"Recorded scan run #{result.run_id} in metadata DB "
            f"({result.findings_written} findings)",
        )
    elif not result.skipped:
        # skipped == expected no-op (e.g. unsupported backend); stay quiet.
        _emit(warn, f"Metadata DB write skipped: {result.error_message}")
    return result


def _emit(fn, message: str) -> None:
    """Print a status line without ever letting an output error escape."""
    try:
        fn(message)
    except Exception:
        pass

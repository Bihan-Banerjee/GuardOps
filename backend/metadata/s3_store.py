"""
backend/metadata/s3_store.py

A read-only MetadataStore backed by an S3 object — the durable bridge that feeds
the v1.0.0 web dashboard (Phase 13).

WHY S3 (not RDS): the EKS cluster is destroyed every night to stay near-zero cost,
so the dashboard's findings must live somewhere that survives `terraform destroy`.
The CLI/CI already produce a full DB dump via MetadataStore.export_json(); this
store reads that dump back. No always-on database, no new server, ~$0/month.

HOW IT WORKS:
  - `guardops db export --to-s3` writes export_json() to
        s3://<bucket>/<prefix>/<project>/latest.json
  - S3MetadataStore fetches that object (cached with a short TTL), hydrates a
    throwaway on-disk SQLite file using the exact schema in sqlite_store._SCHEMA,
    and DELEGATES every read to a SqliteMetadataStore over that file. That reuse
    is the whole point: all the filtering / severity ordering / trend SQL is
    written once and shared, so SQLite and S3 backends can never drift.
  - Writes are no-ops (read-only): persist_report/prune return a skipped result so
    a misconfigured backend still can't break a scan or deploy.

TESTABILITY: the boto3 S3 client can be injected (client=...) so tests run with a
tiny in-memory fake instead of moto or a live bucket.
"""

import json
import os
import sqlite3
import tempfile
import threading
import time
from typing import Optional

from backend.metadata.base import (
    MetadataStore,
    PersistResult,
    PruneResult,
)
from backend.metadata.sqlite_store import SqliteMetadataStore, _SCHEMA

DEFAULT_PREFIX = "metadata"
DEFAULT_TTL_SECONDS = 60


# ── module-level helpers (shared with the CLI export command) ──────────────────

def build_s3_key(prefix: str, project: str) -> str:
    """The S3 key holding a project's latest DB export.

    Per-project so several projects can publish into one bucket without clobbering
    each other. Falls back to <prefix>/latest.json when no project name is set.
    """
    prefix = (prefix or DEFAULT_PREFIX).strip("/")
    project = (project or "").strip().strip("/")
    return f"{prefix}/{project}/latest.json" if project else f"{prefix}/latest.json"


def _s3_client(region: str = "", client=None):
    """Return the injected client, or a real boto3 S3 client. boto3 is a core dep."""
    if client is not None:
        return client
    import boto3
    return boto3.client("s3", region_name=region or None)


def put_export_to_s3(
    data: dict,
    bucket: str,
    key: str,
    *,
    region: str = "",
    client=None,
) -> str:
    """Upload an export_json() dict to S3 and return the s3:// URI."""
    body = json.dumps(data, default=str).encode("utf-8")
    _s3_client(region, client).put_object(
        Bucket=bucket, Key=key, Body=body, ContentType="application/json"
    )
    return f"s3://{bucket}/{key}"


def _hydrate(db_path: str, export: dict) -> None:
    """Recreate `db_path` from an export dict, preserving ids and run_id links."""
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        with conn:
            for r in export.get("runs") or []:
                conn.execute(
                    "INSERT INTO scan_runs (id, project_name, image_ref, git_sha, environment, "
                    "source, timestamp, blocked, fail_on, crit_count, high_count, medium_count, "
                    "low_count, total_count, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        r.get("id"), r.get("project_name", ""), r.get("image_ref", ""),
                        r.get("git_sha", ""), r.get("environment", ""), r.get("source", "scan"),
                        r.get("timestamp", ""), 1 if r.get("blocked") else 0,
                        r.get("fail_on", "HIGH"), r.get("crit_count", 0), r.get("high_count", 0),
                        r.get("medium_count", 0), r.get("low_count", 0), r.get("total_count", 0),
                        r.get("created_at", ""),
                    ),
                )
            for f in export.get("findings") or []:
                conn.execute(
                    "INSERT INTO findings (id, run_id, tool, rule_id, severity, cve, message, "
                    "file_path, line_start, fix_guidance, fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f.get("id"), f.get("run_id"), f.get("tool", ""), f.get("rule_id", ""),
                        f.get("severity", "LOW"), f.get("cve", ""), f.get("message", ""),
                        f.get("file_path", ""), f.get("line_start", 0), f.get("fix_guidance", ""),
                        f.get("fingerprint", ""),
                    ),
                )
            for t in export.get("tool_runs") or []:
                conn.execute(
                    "INSERT INTO tool_runs (id, run_id, tool, success, skipped, skip_reason, "
                    "error_message, crit_count, high_count, medium_count, low_count) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        t.get("id"), t.get("run_id"), t.get("tool", ""),
                        1 if t.get("success") else 0, 1 if t.get("skipped") else 0,
                        t.get("skip_reason", ""), t.get("error_message", ""),
                        t.get("crit_count", 0), t.get("high_count", 0),
                        t.get("medium_count", 0), t.get("low_count", 0),
                    ),
                )
    finally:
        conn.close()


# ── the store ──────────────────────────────────────────────────────────────--

class S3MetadataStore(MetadataStore):
    """Read-only MetadataStore that hydrates from an S3 export object."""

    def __init__(
        self,
        bucket: str,
        *,
        key: Optional[str] = None,
        prefix: str = DEFAULT_PREFIX,
        project: str = "",
        region: str = "",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        client=None,
    ):
        if not bucket:
            raise ValueError("S3MetadataStore requires a bucket name")
        self.bucket = bucket
        self.key = key or build_s3_key(prefix, project)
        self.region = region
        self.ttl_seconds = ttl_seconds
        self._client = client

        self._cache_dir = tempfile.mkdtemp(prefix="guardops-s3-")
        self._db_path = os.path.join(self._cache_dir, "s3cache.db")
        self._lock = threading.Lock()
        self._delegate: Optional[SqliteMetadataStore] = None
        self._fetched_at = 0.0

    @classmethod
    def from_config(cls, config: dict) -> "S3MetadataStore":
        meta = config.get("metadata", {}) or {}
        project = (config.get("project", {}) or {}).get("name", "")
        return cls(
            bucket=meta.get("s3_bucket", ""),
            prefix=meta.get("s3_prefix", DEFAULT_PREFIX),
            project=project,
            region=meta.get("region", ""),
            ttl_seconds=int(meta.get("s3_ttl_seconds", DEFAULT_TTL_SECONDS) or DEFAULT_TTL_SECONDS),
        )

    # ── fetch + refresh ────────────────────────────────────────────────────────
    def _fetch_export(self) -> dict:
        """Read the export object from S3. A missing object means 'no data yet'
        (empty result); any other S3 error propagates so callers can report it."""
        client = _s3_client(self.region, self._client)
        try:
            obj = client.get_object(Bucket=self.bucket, Key=self.key)
        except Exception as e:  # noqa: BLE001 - inspect for the not-found case
            if _is_not_found(e):
                return {"runs": [], "findings": [], "tool_runs": []}
            raise
        return json.loads(obj["Body"].read())

    def _fresh(self) -> SqliteMetadataStore:
        """Return a SqliteMetadataStore over a freshly-hydrated local cache."""
        with self._lock:
            expired = (time.time() - self._fetched_at) > self.ttl_seconds
            if self._delegate is None or expired:
                export = self._fetch_export()
                _hydrate(self._db_path, export)
                self._delegate = SqliteMetadataStore(self._db_path)
                self._fetched_at = time.time()
            return self._delegate

    # ── reads (delegated) ───────────────────────────────────────────────────────
    def list_runs(self, *, project=None, environment=None, image=None, limit=50):
        return self._fresh().list_runs(project=project, environment=environment, image=image, limit=limit)

    def get_run(self, run_id):
        return self._fresh().get_run(run_id)

    def latest_run_before(self, *, run_id=None, project=None, image=None):
        return self._fresh().latest_run_before(run_id=run_id, project=project, image=image)

    def query_findings(self, *, run_id=None, severity=None, tool=None, cve=None, image=None, limit=200):
        return self._fresh().query_findings(
            run_id=run_id, severity=severity, tool=tool, cve=cve, image=image, limit=limit
        )

    def severity_trends(self, *, project=None, environment=None, days=30):
        return self._fresh().severity_trends(project=project, environment=environment, days=days)

    def export_json(self) -> dict:
        return self._fresh().export_json()

    # ── writes (no-ops: this store is read-only) ─────────────────────────────────
    def init_schema(self) -> None:
        return None

    def persist_report(self, report, *, environment="", git_sha="", source="scan") -> PersistResult:
        return PersistResult(success=False, skipped=True, skip_reason="S3 metadata store is read-only")

    def prune(self, *, keep_days=None, keep_last=None) -> PruneResult:
        return PruneResult(success=False, error_message="S3 metadata store is read-only")


def _is_not_found(exc: Exception) -> bool:
    """True if an S3 get_object error means the key does not exist yet."""
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code", "")
    name = exc.__class__.__name__
    return code in ("NoSuchKey", "404", "NotFound") or name in ("NoSuchKey", "NotFound")

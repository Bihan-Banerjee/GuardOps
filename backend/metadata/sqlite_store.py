"""
backend/metadata/sqlite_store.py

The default Phase 12 storage backend: a single SQLite file (stdlib sqlite3, no
new dependencies). Chosen because it needs zero infrastructure, works offline in
the local k3d loop, survives the nightly `terraform destroy`, and the query
commands (history/findings/trends/diff) are naturally relational.

Schema is portable SQL created on first connect via CREATE TABLE IF NOT EXISTS —
no migration framework. The store is single-writer (one CLI process at a time),
which fits a CLI perfectly; the eventual multi-writer dashboard will use the
Postgres implementation of the same MetadataStore interface.

NOTE: a new connection is opened per operation and closed after, so the db_path
must be a real file (or a shared file). ":memory:" databases are per-connection
and will not persist across calls — use a tmp file in tests.
"""

import hashlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from backend.metadata.base import (
    SEVERITY_ORDER,
    FindingRecord,
    MetadataStore,
    PersistResult,
    PruneResult,
    RunRecord,
    TrendPoint,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name  TEXT    NOT NULL,
    image_ref     TEXT    NOT NULL DEFAULT '',
    git_sha       TEXT    NOT NULL DEFAULT '',
    environment   TEXT    NOT NULL DEFAULT '',
    source        TEXT    NOT NULL DEFAULT 'scan',
    timestamp     TEXT    NOT NULL,
    blocked       INTEGER NOT NULL DEFAULT 0,
    fail_on       TEXT    NOT NULL DEFAULT 'HIGH',
    crit_count    INTEGER NOT NULL DEFAULT 0,
    high_count    INTEGER NOT NULL DEFAULT 0,
    medium_count  INTEGER NOT NULL DEFAULT 0,
    low_count     INTEGER NOT NULL DEFAULT 0,
    total_count   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_project_ts ON scan_runs(project_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_runs_image      ON scan_runs(image_ref);
CREATE INDEX IF NOT EXISTS idx_runs_env_ts     ON scan_runs(environment, timestamp);

CREATE TABLE IF NOT EXISTS findings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    tool         TEXT    NOT NULL,
    rule_id      TEXT    NOT NULL DEFAULT '',
    severity     TEXT    NOT NULL DEFAULT 'LOW',
    cve          TEXT    NOT NULL DEFAULT '',
    message      TEXT    NOT NULL DEFAULT '',
    file_path    TEXT    NOT NULL DEFAULT '',
    line_start   INTEGER NOT NULL DEFAULT 0,
    fix_guidance TEXT    NOT NULL DEFAULT '',
    fingerprint  TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_find_run  ON findings(run_id);
CREATE INDEX IF NOT EXISTS idx_find_sev  ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_find_cve  ON findings(cve);
CREATE INDEX IF NOT EXISTS idx_find_tool ON findings(tool);
CREATE INDEX IF NOT EXISTS idx_find_fp   ON findings(fingerprint);

CREATE TABLE IF NOT EXISTS tool_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    tool          TEXT    NOT NULL,
    success       INTEGER NOT NULL DEFAULT 0,
    skipped       INTEGER NOT NULL DEFAULT 0,
    skip_reason   TEXT    NOT NULL DEFAULT '',
    error_message TEXT    NOT NULL DEFAULT '',
    crit_count    INTEGER NOT NULL DEFAULT 0,
    high_count    INTEGER NOT NULL DEFAULT 0,
    medium_count  INTEGER NOT NULL DEFAULT 0,
    low_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tool_run ON tool_runs(run_id);

CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '1');
"""


class SqliteMetadataStore(MetadataStore):
    SCHEMA_VERSION = "1"

    def __init__(self, db_path: str):
        self.db_path = str(db_path)

    # ── connection / schema ───────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        parent = Path(self.db_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)  # idempotent — CREATE/INSERT ... IF NOT EXISTS/OR IGNORE
        return conn

    def init_schema(self) -> None:
        self._connect().close()

    # ── write ─────────────────────────────────────────────────────────────────
    def persist_report(self, report, *, environment="", git_sha="", source="scan") -> PersistResult:
        try:
            counts = report.severity_counts
            findings = report.all_findings
            total = sum(counts.values())
            created_at = datetime.utcnow().isoformat()

            conn = self._connect()
            try:
                with conn:  # commits on success, rolls back on exception
                    cur = conn.execute(
                        """INSERT INTO scan_runs
                           (project_name, image_ref, git_sha, environment, source, timestamp,
                            blocked, fail_on, crit_count, high_count, medium_count, low_count,
                            total_count, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            report.project_name,
                            report.image_ref,
                            git_sha,
                            environment,
                            source,
                            report.timestamp,
                            1 if report.blocked else 0,
                            report.fail_on_severity,
                            counts.get("CRITICAL", 0),
                            counts.get("HIGH", 0),
                            counts.get("MEDIUM", 0),
                            counts.get("LOW", 0),
                            total,
                            created_at,
                        ),
                    )
                    run_id = cur.lastrowid

                    finding_rows = [
                        (
                            run_id, f.tool, f.rule_id, f.severity, f.cve, f.message,
                            f.file_path, f.line_start, f.fix_guidance, self._fingerprint(f),
                        )
                        for f in findings
                    ]
                    if finding_rows:
                        conn.executemany(
                            """INSERT INTO findings
                               (run_id, tool, rule_id, severity, cve, message, file_path,
                                line_start, fix_guidance, fingerprint)
                               VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            finding_rows,
                        )

                    tool_rows = [
                        (
                            run_id, r.tool, 1 if r.success else 0, 1 if r.skipped else 0,
                            r.skip_reason, r.error_message, r.critical_count, r.high_count,
                            r.medium_count, r.low_count,
                        )
                        for r in report.scan_results
                    ]
                    if tool_rows:
                        conn.executemany(
                            """INSERT INTO tool_runs
                               (run_id, tool, success, skipped, skip_reason, error_message,
                                crit_count, high_count, medium_count, low_count)
                               VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            tool_rows,
                        )
                return PersistResult(success=True, run_id=run_id, findings_written=len(finding_rows))
            finally:
                conn.close()
        except Exception as e:  # never break a scan/deploy
            return PersistResult(success=False, error_message=str(e))

    # ── read ──────────────────────────────────────────────────────────────────
    def list_runs(self, *, project=None, environment=None, image=None, limit=50) -> list[RunRecord]:
        clauses: list[str] = []
        params: list = []
        if project:
            clauses.append("project_name = ?")
            params.append(project)
        if environment:
            clauses.append("environment = ?")
            params.append(environment)
        if image:
            clauses.append("image_ref = ?")
            params.append(image)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM scan_runs{where} ORDER BY id DESC LIMIT ?", params
            ).fetchall()
            return [self._row_to_run(r) for r in rows]
        finally:
            conn.close()

    def get_run(self, run_id: int) -> Optional[RunRecord]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
            return self._row_to_run(row) if row else None
        finally:
            conn.close()

    def latest_run_before(self, *, run_id=None, project=None, image=None) -> Optional[RunRecord]:
        clauses: list[str] = []
        params: list = []
        if run_id is not None:
            clauses.append("id < ?")
            params.append(run_id)
        if project:
            clauses.append("project_name = ?")
            params.append(project)
        if image:
            clauses.append("image_ref = ?")
            params.append(image)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT * FROM scan_runs{where} ORDER BY id DESC LIMIT 1", params
            ).fetchone()
            return self._row_to_run(row) if row else None
        finally:
            conn.close()

    def query_findings(self, *, run_id=None, severity=None, tool=None, cve=None, image=None, limit=200) -> list[FindingRecord]:
        clauses: list[str] = []
        params: list = []
        join = ""
        if image:
            join = " JOIN scan_runs r ON f.run_id = r.id"
            clauses.append("r.image_ref = ?")
            params.append(image)
        if run_id is not None:
            clauses.append("f.run_id = ?")
            params.append(run_id)
        if severity:
            sev = severity.upper()
            if sev in SEVERITY_ORDER:
                allowed = SEVERITY_ORDER[SEVERITY_ORDER.index(sev):]
                placeholders = ",".join("?" * len(allowed))
                clauses.append(f"f.severity IN ({placeholders})")
                params.extend(allowed)
        if tool:
            clauses.append("f.tool = ?")
            params.append(tool)
        if cve:
            clauses.append("f.cve = ?")
            params.append(cve)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT f.* FROM findings f{join}{where} "
                "ORDER BY CASE f.severity "
                "WHEN 'CRITICAL' THEN 4 WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2 "
                "WHEN 'LOW' THEN 1 ELSE 0 END DESC, f.run_id DESC, f.id ASC LIMIT ?",
                params,
            ).fetchall()
            return [self._row_to_finding(r) for r in rows]
        finally:
            conn.close()

    def severity_trends(self, *, project=None, environment=None, days=30) -> list[TrendPoint]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        clauses = ["substr(timestamp, 1, 10) >= ?"]
        params: list = [cutoff]
        if project:
            clauses.append("project_name = ?")
            params.append(project)
        if environment:
            clauses.append("environment = ?")
            params.append(environment)
        where = " WHERE " + " AND ".join(clauses)
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT substr(timestamp, 1, 10) AS day, "
                "SUM(crit_count) AS crit, SUM(high_count) AS high, "
                "SUM(medium_count) AS medium, SUM(low_count) AS low, "
                "SUM(total_count) AS total, COUNT(*) AS runs "
                f"FROM scan_runs{where} GROUP BY day ORDER BY day ASC",
                params,
            ).fetchall()
            return [
                TrendPoint(
                    date=r["day"], crit=r["crit"] or 0, high=r["high"] or 0,
                    medium=r["medium"] or 0, low=r["low"] or 0,
                    total=r["total"] or 0, runs=r["runs"] or 0,
                )
                for r in rows
            ]
        finally:
            conn.close()

    # ── maintenance ───────────────────────────────────────────────────────────
    def prune(self, *, keep_days=None, keep_last=None) -> PruneResult:
        try:
            conn = self._connect()
            try:
                if keep_last is not None and keep_last > 0:
                    keep_ids = [
                        row["id"] for row in conn.execute(
                            "SELECT id FROM scan_runs ORDER BY id DESC LIMIT ?", (keep_last,)
                        ).fetchall()
                    ]
                    if not keep_ids:
                        return PruneResult(success=True)
                    ph = ",".join("?" * len(keep_ids))
                    fcount = conn.execute(
                        f"SELECT COUNT(*) AS c FROM findings WHERE run_id NOT IN ({ph})", keep_ids
                    ).fetchone()["c"]
                    rcount = conn.execute(
                        f"SELECT COUNT(*) AS c FROM scan_runs WHERE id NOT IN ({ph})", keep_ids
                    ).fetchone()["c"]
                    with conn:
                        conn.execute(f"DELETE FROM scan_runs WHERE id NOT IN ({ph})", keep_ids)
                    return PruneResult(success=True, runs_deleted=rcount, findings_deleted=fcount)

                if keep_days is not None and keep_days > 0:
                    cutoff = (datetime.utcnow() - timedelta(days=keep_days)).isoformat()
                    fcount = conn.execute(
                        "SELECT COUNT(*) AS c FROM findings WHERE run_id IN "
                        "(SELECT id FROM scan_runs WHERE timestamp < ?)", (cutoff,)
                    ).fetchone()["c"]
                    rcount = conn.execute(
                        "SELECT COUNT(*) AS c FROM scan_runs WHERE timestamp < ?", (cutoff,)
                    ).fetchone()["c"]
                    with conn:
                        conn.execute("DELETE FROM scan_runs WHERE timestamp < ?", (cutoff,))
                    return PruneResult(success=True, runs_deleted=rcount, findings_deleted=fcount)

                return PruneResult(success=True)  # nothing to do (keep forever)
            finally:
                conn.close()
        except Exception as e:
            return PruneResult(success=False, error_message=str(e))

    def export_json(self) -> dict:
        conn = self._connect()
        try:
            runs = [
                self._row_to_run(r).to_dict()
                for r in conn.execute("SELECT * FROM scan_runs ORDER BY id").fetchall()
            ]
            findings = [
                self._row_to_finding(r).to_dict()
                for r in conn.execute("SELECT * FROM findings ORDER BY id").fetchall()
            ]
            tool_runs = [dict(r) for r in conn.execute("SELECT * FROM tool_runs ORDER BY id").fetchall()]
            version_row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            return {
                "schema_version": version_row["value"] if version_row else self.SCHEMA_VERSION,
                "exported_at": datetime.utcnow().isoformat(),
                "runs": runs,
                "findings": findings,
                "tool_runs": tool_runs,
            }
        finally:
            conn.close()

    # ── helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _fingerprint(finding) -> str:
        raw = f"{finding.tool}|{finding.rule_id}|{finding.cve}|{finding.file_path}|{finding.severity}"
        # Dedup fingerprint only — not a security/integrity digest. usedforsecurity=False
        # documents that intent (and silences SAST) while keeping the digest identical.
        return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()

    @staticmethod
    def _row_to_run(row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            project_name=row["project_name"],
            image_ref=row["image_ref"],
            git_sha=row["git_sha"],
            environment=row["environment"],
            source=row["source"],
            timestamp=row["timestamp"],
            blocked=bool(row["blocked"]),
            fail_on=row["fail_on"],
            crit_count=row["crit_count"],
            high_count=row["high_count"],
            medium_count=row["medium_count"],
            low_count=row["low_count"],
            total_count=row["total_count"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_finding(row) -> FindingRecord:
        return FindingRecord(
            id=row["id"],
            run_id=row["run_id"],
            tool=row["tool"],
            rule_id=row["rule_id"],
            severity=row["severity"],
            cve=row["cve"],
            message=row["message"],
            file_path=row["file_path"],
            line_start=row["line_start"],
            fix_guidance=row["fix_guidance"],
            fingerprint=row["fingerprint"],
        )

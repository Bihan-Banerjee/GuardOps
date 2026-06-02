"""
backend/metadata/base.py

The storage-backend contract for scan metadata, plus the small set of
JSON-serializable record types the CLI commands consume.

WHY an abstraction: Phase 12 ships a SQLite store (zero infra, perfect for the
local k3d loop and CI). The v1.0.0 dashboard on guardops.live will want a shared
networked database (Postgres). By making every command depend on MetadataStore —
never on sqlite3 directly — that swap is a new subclass + a factory line, not a
rewrite. The dataclasses below mirror the convention used elsewhere in the
backend (ScanResult, VerifyResult): plain dataclasses with a to_dict() so they
serialize cleanly behind a `--json-output` flag.
"""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Optional

# Shared ordering for severity thresholds. Matches ScanResult.has_blocking_findings
# in backend/security/semgrep_runner.py so `findings --severity HIGH` and the
# scan gate agree on what "HIGH or above" means.
SEVERITY_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


@dataclass
class PersistResult:
    """Outcome of writing one ConsolidatedReport. success=False is never fatal —
    the caller (persist_report_safe) logs a warning and the scan/deploy proceeds."""
    success: bool
    run_id: Optional[int] = None
    findings_written: int = 0
    error_message: str = ""
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunRecord:
    """One persisted scan run (a row in scan_runs)."""
    id: int
    project_name: str
    image_ref: str
    git_sha: str
    environment: str
    source: str
    timestamp: str
    blocked: bool
    fail_on: str
    crit_count: int
    high_count: int
    medium_count: int
    low_count: int
    total_count: int
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FindingRecord:
    """One persisted finding (a row in findings), linked to a RunRecord by run_id."""
    id: int
    run_id: int
    tool: str
    rule_id: str
    severity: str
    cve: str
    message: str
    file_path: str
    line_start: int
    fix_guidance: str
    fingerprint: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrendPoint:
    """Severity counts rolled up for a single calendar day (UTC)."""
    date: str
    crit: int
    high: int
    medium: int
    low: int
    total: int
    runs: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PruneResult:
    """Outcome of a retention prune."""
    success: bool
    runs_deleted: int = 0
    findings_deleted: int = 0
    error_message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class MetadataStore(ABC):
    """
    Storage-backend contract for scan metadata.

    Implementations must never raise out of persist_report (a DB hiccup must not
    break a deploy) — return PersistResult(success=False, ...) instead. The read
    methods may raise; callers run them inside CLI commands that handle errors.
    """

    @abstractmethod
    def init_schema(self) -> None:
        """Create tables/indexes if they do not exist. Safe to call repeatedly."""

    @abstractmethod
    def persist_report(
        self,
        report,
        *,
        environment: str = "",
        git_sha: str = "",
        source: str = "scan",
    ) -> PersistResult:
        """Persist a backend.security.report_generator.ConsolidatedReport."""

    @abstractmethod
    def list_runs(
        self,
        *,
        project: Optional[str] = None,
        environment: Optional[str] = None,
        image: Optional[str] = None,
        limit: int = 50,
    ) -> list[RunRecord]:
        """Most-recent-first list of scan runs, optionally filtered."""

    @abstractmethod
    def get_run(self, run_id: int) -> Optional[RunRecord]:
        """Fetch a single run by id, or None."""

    @abstractmethod
    def latest_run_before(
        self,
        *,
        run_id: Optional[int] = None,
        project: Optional[str] = None,
        image: Optional[str] = None,
    ) -> Optional[RunRecord]:
        """The newest run older than run_id (for the same project/image when given).
        Used by `guardops diff` to pick a baseline automatically."""

    @abstractmethod
    def query_findings(
        self,
        *,
        run_id: Optional[int] = None,
        severity: Optional[str] = None,
        tool: Optional[str] = None,
        cve: Optional[str] = None,
        image: Optional[str] = None,
        limit: int = 200,
    ) -> list[FindingRecord]:
        """Findings filtered by run/severity-threshold/tool/cve/image."""

    @abstractmethod
    def severity_trends(
        self,
        *,
        project: Optional[str] = None,
        environment: Optional[str] = None,
        days: int = 30,
    ) -> list[TrendPoint]:
        """Per-day severity rollup over the last `days` days, oldest first."""

    @abstractmethod
    def prune(
        self,
        *,
        keep_days: Optional[int] = None,
        keep_last: Optional[int] = None,
    ) -> PruneResult:
        """Delete old runs by age (keep_days) or count (keep_last). Findings cascade."""

    @abstractmethod
    def export_json(self) -> dict:
        """Dump the whole store as a JSON-serializable dict (dashboard/migration bridge)."""


class NullMetadataStore(MetadataStore):
    """
    No-op store returned by the factory when the configured backend is unknown or
    unsupported. Keeps Phase 12 non-fatal: persistence silently skips and reads
    return empty, with a single explanatory reason. Never touches disk.
    """

    def __init__(self, reason: str = "metadata backend not available"):
        self.reason = reason

    def init_schema(self) -> None:
        return None

    def persist_report(self, report, *, environment="", git_sha="", source="scan") -> PersistResult:
        return PersistResult(success=False, skipped=True, skip_reason=self.reason)

    def list_runs(self, *, project=None, environment=None, image=None, limit=50) -> list[RunRecord]:
        return []

    def get_run(self, run_id: int) -> Optional[RunRecord]:
        return None

    def latest_run_before(self, *, run_id=None, project=None, image=None) -> Optional[RunRecord]:
        return None

    def query_findings(self, *, run_id=None, severity=None, tool=None, cve=None, image=None, limit=200) -> list[FindingRecord]:
        return []

    def severity_trends(self, *, project=None, environment=None, days=30) -> list[TrendPoint]:
        return []

    def prune(self, *, keep_days=None, keep_last=None) -> PruneResult:
        return PruneResult(success=False, error_message=self.reason)

    def export_json(self) -> dict:
        return {"runs": [], "findings": [], "tool_runs": [], "note": self.reason}

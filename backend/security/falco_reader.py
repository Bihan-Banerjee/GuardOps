"""
backend/security/falco_reader.py

Reads Falco runtime security alerts from Loki.

Phase 7 — Runtime Security.

Falco watches kernel-level syscall events inside every pod (execve,
connect, open) and emits structured JSON logs. Those logs are shipped
to Loki by a Promtail DaemonSet. This module queries Loki's HTTP API
(LogQL) and normalises the results into the same four-tier severity
scale used throughout GuardOps (CRITICAL > HIGH > MEDIUM > LOW).

Falco priority → GuardOps severity:
  EMERGENCY / ALERT / CRITICAL  →  CRITICAL
  ERROR                          →  HIGH
  WARNING                        →  MEDIUM
  NOTICE / INFORMATIONAL / INFO  →  LOW
  DEBUG                          →  LOW  (filtered out at source by Falco config)

Design mirrors semgrep_runner.py / zap_runner.py:
  - Named dataclass for each result type
  - Severity properties (critical_count, high_count, …)
  - has_alerts_above(severity) mirrors has_blocking_findings()
  - All errors surfaced in error_message, never raised to caller
"""

import json
import time
import datetime
from dataclasses import dataclass, field
from typing import Optional

import requests


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class FalcoAlert:
    """A single runtime security event captured by Falco."""
    rule: str                  # Falco rule name, e.g. "Shell Spawned Inside Container"
    priority: str              # Raw Falco priority: CRITICAL, ERROR, WARNING, etc.
    severity: str              # GuardOps scale: CRITICAL | HIGH | MEDIUM | LOW
    output: str                # Falco human-readable event description
    pod_name: str              # k8s.pod.name from Falco output_fields
    namespace: str             # k8s.ns.name from Falco output_fields
    container_name: str        # container.name from Falco output_fields
    timestamp: str             # ISO-8601 from Falco (or Loki ingestion time)
    tags: list[str] = field(default_factory=list)


@dataclass
class FalcoQueryResult:
    """Result of a Loki query for Falco alerts — mirrors ZapScanResult interface."""
    success: bool
    alerts: list[FalcoAlert] = field(default_factory=list)
    error_message: str = ""
    skipped: bool = False
    skip_reason: str = ""
    query_duration_seconds: float = 0.0
    loki_url: str = ""
    time_window: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for a in self.alerts if a.severity == "CRITICAL")

    @property
    def high_count(self) -> int:
        return sum(1 for a in self.alerts if a.severity == "HIGH")

    @property
    def medium_count(self) -> int:
        return sum(1 for a in self.alerts if a.severity == "MEDIUM")

    @property
    def low_count(self) -> int:
        return sum(1 for a in self.alerts if a.severity == "LOW")

    @property
    def severity_counts(self) -> dict[str, int]:
        return {
            "CRITICAL": self.critical_count,
            "HIGH":     self.high_count,
            "MEDIUM":   self.medium_count,
            "LOW":      self.low_count,
        }

    def has_alerts_above(self, severity: str = "HIGH") -> bool:
        """
        Returns True if any alert is at or above the given severity.
        Mirrors ScanResult.has_blocking_findings() from semgrep_runner.py.
        """
        order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
        threshold = order.index(severity.upper())
        return any(order.index(a.severity) >= threshold for a in self.alerts)


# ── Severity mapping ──────────────────────────────────────────────────────────

# Falco uses syslog-style priority names. Map to GuardOps 4-tier scale.
# Reference: https://falco.org/docs/rules/conditions-and-expressions/#priority
FALCO_PRIORITY_MAP: dict[str, str] = {
    "EMERGENCY":     "CRITICAL",
    "ALERT":         "CRITICAL",
    "CRITICAL":      "CRITICAL",
    "ERROR":         "HIGH",
    "WARNING":       "MEDIUM",
    "NOTICE":        "LOW",
    "INFORMATIONAL": "LOW",
    "INFO":          "LOW",
    "DEBUG":         "LOW",
}

# Human-readable time windows → seconds offset for Loki start parameter.
TIME_WINDOW_SECONDS: dict[str, int] = {
    "15m":  15 * 60,
    "30m":  30 * 60,
    "1h":   1  * 3600,
    "3h":   3  * 3600,
    "6h":   6  * 3600,
    "12h":  12 * 3600,
    "24h":  24 * 3600,
    "7d":   7  * 86400,
}


# ── Public API ────────────────────────────────────────────────────────────────

def query_falco_alerts(
    loki_url: str,
    time_window: str = "1h",
    namespace_filter: Optional[str] = None,
    min_severity: str = "LOW",
    limit: int = 200,
    timeout_seconds: int = 30,
) -> FalcoQueryResult:
    """
    Query Loki for Falco runtime security alerts.

    Uses Loki's /loki/api/v1/query_range endpoint with a LogQL stream
    selector targeting the {app="falco"} label applied by the Falco Helm
    chart. Promtail ships these logs to Loki automatically.

    Args:
        loki_url:         Base URL of the Loki service.
                          Typical values:
                            - "http://localhost:3100"  (after port-forward)
                            - "http://loki.monitoring.svc.cluster.local:3100"
        time_window:      How far back to look. One of: 15m, 30m, 1h, 3h,
                          6h, 12h, 24h, 7d. Default: 1h.
        namespace_filter: If set, only return alerts whose k8s namespace
                          matches. Applied after retrieval (LogQL-level
                          filtering for namespace would require pipeline
                          expressions that differ across Loki versions).
        min_severity:     Minimum severity to include. LOW = return all.
        limit:            Max log lines to fetch from Loki in one request.
                          Loki default cap is 5000; 200 is safe for display.
        timeout_seconds:  HTTP request timeout.

    Returns:
        FalcoQueryResult. On any failure (connection error, bad JSON,
        non-200 response), success=False with error_message populated.
        The caller should check success before accessing alerts.
    """
    if not loki_url:
        return FalcoQueryResult(
            success=False,
            skipped=True,
            skip_reason=(
                "Loki URL not configured. "
                "Set monitoring.loki_url in .guardops.yaml or pass --loki-url. "
                "Quick start: kubectl port-forward svc/loki 3100:3100 -n monitoring"
            ),
        )

    start_time = time.time()

    # LogQL: select all logs from the falco app label, parse as JSON so
    # Loki indexes the structured fields (priority, rule, output_fields).
    logql = '{app="falco"} | json'

    window_seconds = TIME_WINDOW_SECONDS.get(time_window, 3600)
    now_ns   = int(time.time() * 1_000_000_000)
    start_ns = int((time.time() - window_seconds) * 1_000_000_000)

    params = {
        "query":     logql,
        "start":     str(start_ns),
        "end":       str(now_ns),
        "limit":     str(limit),
        "direction": "backward",   # most recent alerts first
    }

    endpoint = f"{loki_url.rstrip('/')}/loki/api/v1/query_range"

    # ── HTTP request ──────────────────────────────────────────────────────────
    try:
        response = requests.get(
            endpoint,
            params=params,
            timeout=timeout_seconds,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()

    except requests.exceptions.ConnectionError:
        return FalcoQueryResult(
            success=False,
            loki_url=loki_url,
            error_message=(
                f"Cannot connect to Loki at {loki_url}. "
                "Is Loki deployed and reachable? "
                "Run: kubectl port-forward svc/loki 3100:3100 -n monitoring"
            ),
        )
    except requests.exceptions.Timeout:
        return FalcoQueryResult(
            success=False,
            loki_url=loki_url,
            error_message=f"Loki query timed out after {timeout_seconds}s.",
        )
    except requests.exceptions.HTTPError as exc:
        return FalcoQueryResult(
            success=False,
            loki_url=loki_url,
            error_message=(
                f"Loki returned HTTP {response.status_code}: {exc}. "
                f"Body: {response.text[:200]}"
            ),
        )
    except Exception as exc:
        return FalcoQueryResult(
            success=False,
            loki_url=loki_url,
            error_message=f"Unexpected error querying Loki: {exc}",
        )

    # ── Parse response ────────────────────────────────────────────────────────
    try:
        data = response.json()
    except json.JSONDecodeError:
        return FalcoQueryResult(
            success=False,
            loki_url=loki_url,
            error_message=(
                f"Could not parse Loki response as JSON. "
                f"Got: {response.text[:300]}"
            ),
        )

    # Loki query_range response structure:
    # {
    #   "status": "success",
    #   "data": {
    #     "resultType": "streams",
    #     "result": [
    #       {
    #         "stream": {"app": "falco", "namespace": "..."},
    #         "values": [
    #           ["<unix_nanoseconds>", "<log_line_string>"],
    #           ...
    #         ]
    #       }
    #     ]
    #   }
    # }

    if data.get("status") != "success":
        return FalcoQueryResult(
            success=False,
            loki_url=loki_url,
            error_message=(
                f"Loki returned non-success status: '{data.get('status', 'unknown')}'. "
                f"Full response: {str(data)[:300]}"
            ),
        )

    alerts = _parse_loki_response(
        data=data,
        namespace_filter=namespace_filter,
        min_severity=min_severity,
    )

    duration = time.time() - start_time

    return FalcoQueryResult(
        success=True,
        alerts=alerts,
        query_duration_seconds=duration,
        loki_url=loki_url,
        time_window=time_window,
    )


# ── Internal parsing ──────────────────────────────────────────────────────────

def _parse_loki_response(
    data: dict,
    namespace_filter: Optional[str],
    min_severity: str,
) -> list[FalcoAlert]:
    """
    Walks the Loki query_range response and converts each log line into
    a FalcoAlert, applying severity and namespace filters.

    Falco JSON output format (with json_output=true in falco config):
    {
      "hostname":  "ip-10-0-1-45",
      "output":    "Shell spawned (user=root shell=bash ...)",
      "priority":  "Critical",
      "rule":      "Shell Spawned Inside Container",
      "time":      "2024-06-15T09:23:11.000000001Z",
      "output_fields": {
        "container.name":  "my-app",
        "k8s.ns.name":     "default",
        "k8s.pod.name":    "my-app-7d9f6b-xk2pq",
        "proc.cmdline":    "bash",
        "user.name":       "root"
      }
    }
    """
    severity_order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    min_index = severity_order.index(min_severity.upper())
    alerts: list[FalcoAlert] = []

    for stream in data.get("data", {}).get("result", []):
        for timestamp_ns_str, log_line in stream.get("values", []):
            alert = _parse_falco_log_line(log_line, timestamp_ns_str)
            if alert is None:
                continue

            # Apply severity floor
            if severity_order.index(alert.severity) < min_index:
                continue

            # Apply namespace filter (post-retrieval — avoids LogQL complexity)
            if namespace_filter and alert.namespace not in (namespace_filter, "unknown"):
                continue

            alerts.append(alert)

    # Sort: most severe first, then most recent first within same severity.
    severity_rank = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
    alerts.sort(
        key=lambda a: (severity_rank.get(a.severity, 0), a.timestamp),
        reverse=True,
    )

    return alerts


def _parse_falco_log_line(
    log_line: str,
    timestamp_ns_str: str,
) -> Optional[FalcoAlert]:
    """
    Parses one Falco JSON log line into a FalcoAlert.

    Returns None for:
      - Non-JSON lines (Falco startup/config messages)
      - Lines missing required fields (priority, rule)
      - Malformed JSON
    """
    log_line = log_line.strip()
    if not log_line or not log_line.startswith("{"):
        return None

    try:
        event = json.loads(log_line)
    except json.JSONDecodeError:
        return None

    # Both 'priority' and 'rule' are required Falco fields.
    priority_raw = event.get("priority", "")
    rule = event.get("rule", "")
    if not priority_raw or not rule:
        return None

    priority = priority_raw.upper()
    severity = FALCO_PRIORITY_MAP.get(priority, "LOW")

    output_fields: dict = event.get("output_fields") or {}

    # Falco field names use dot notation in JSON output_fields.
    # We try both dot-notation keys and underscore variants for compatibility
    # across Falco versions.
    pod_name = (
        output_fields.get("k8s.pod.name")
        or output_fields.get("k8s_pod_name")
        or "unknown"
    )
    namespace = (
        output_fields.get("k8s.ns.name")
        or output_fields.get("k8s_ns_name")
        or "unknown"
    )
    container_name = (
        output_fields.get("container.name")
        or output_fields.get("container_name")
        or "unknown"
    )

    # Falco tags are comma-separated strings in some versions,
    # list-of-strings in others.
    raw_tags = event.get("tags") or output_fields.get("falco.tags") or ""
    if isinstance(raw_tags, list):
        tags = [t.strip() for t in raw_tags if t.strip()]
    elif isinstance(raw_tags, str):
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    else:
        tags = []

    # Prefer Falco's own high-precision timestamp over Loki's ingestion time.
    timestamp = event.get("time") or _ns_to_iso(timestamp_ns_str)

    return FalcoAlert(
        rule=rule,
        priority=priority,
        severity=severity,
        output=event.get("output", ""),
        pod_name=pod_name,
        namespace=namespace,
        container_name=container_name,
        timestamp=timestamp,
        tags=tags,
    )


def _ns_to_iso(timestamp_ns_str: str) -> str:
    """
    Converts a Loki nanosecond Unix timestamp string to ISO-8601.
    e.g. "1718441591000000001" → "2024-06-15T09:23:11Z"
    """
    try:
        ts_seconds = int(timestamp_ns_str) / 1_000_000_000
        return datetime.datetime.utcfromtimestamp(ts_seconds).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (ValueError, TypeError):
        return timestamp_ns_str

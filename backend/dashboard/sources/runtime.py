"""
backend/dashboard/sources/runtime.py

Runtime security alerts (Falco via Loki). A thin wrapper over the existing
backend.security.falco_reader.query_falco_alerts so the dashboard and
`guardops runtime-status` share one query path. Unreachable Loki → available:False.
"""

from dataclasses import asdict

from backend.security.falco_reader import query_falco_alerts


def runtime_alerts(settings, since: str = "1h", namespace=None,
                   min_severity: str = "LOW", limit: int = 200) -> dict:
    if not settings.loki_url:
        return {"available": False, "reason": "loki_url not configured"}

    result = query_falco_alerts(
        settings.loki_url,
        time_window=since,
        namespace_filter=namespace,
        min_severity=min_severity,
        limit=limit,
    )
    if not result.success:
        return {
            "available": False,
            "reason": result.error_message or result.skip_reason or "Loki query failed",
            "loki_url": settings.loki_url,
        }
    return {
        "available": True,
        "loki_url": settings.loki_url,
        "window": since,
        "counts": result.severity_counts,
        "alerts": [asdict(a) for a in result.alerts],
    }

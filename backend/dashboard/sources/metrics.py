"""
backend/dashboard/sources/metrics.py

Observability data from Prometheus (the kube-prometheus-stack the project already
runs). Uses the instant-query HTTP API and the same `requests` client the rest of
the backend uses (falco_reader, gitops_writer). Any failure → {"available": False,
"reason": ...} so the dashboard degrades gracefully when the cluster is down.
"""

import requests


def _query(prometheus_url: str, promql: str, timeout: int = 10):
    """Run a Prometheus instant query, returning the `result` vector. Raises on error."""
    url = prometheus_url.rstrip("/") + "/api/v1/query"
    resp = requests.get(url, params={"query": promql}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {data.get('error', 'unknown error')}")
    return data.get("data", {}).get("result", [])


def app_metrics(settings) -> dict:
    """The custom guardops_* application metrics (request rate, latency, build info)."""
    url = settings.prometheus_url
    if not url:
        return {"available": False, "reason": "prometheus_url not configured"}
    try:
        return {
            "available": True,
            "prometheus_url": url,
            "request_rate": _query(url, "sum(rate(guardops_requests_total[5m])) by (path)"),
            "request_latency_ms": _query(url, "guardops_request_duration_ms"),
            "app_info": _query(url, "guardops_app_info"),
        }
    except Exception as e:  # noqa: BLE001 - degrade, never crash the route
        return {"available": False, "reason": str(e), "prometheus_url": url}


def resource_metrics(settings, namespace: str = "default") -> dict:
    """Pod-level CPU and memory usage for a namespace (container_* series)."""
    url = settings.prometheus_url
    if not url:
        return {"available": False, "reason": "prometheus_url not configured"}
    cpu_q = (
        'sum(rate(container_cpu_usage_seconds_total{namespace="' + namespace + '"}[5m])) by (pod)'
    )
    mem_q = 'sum(container_memory_usage_bytes{namespace="' + namespace + '"}) by (pod)'
    try:
        return {
            "available": True,
            "prometheus_url": url,
            "namespace": namespace,
            "cpu_cores": _query(url, cpu_q),
            "memory_bytes": _query(url, mem_q),
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e), "prometheus_url": url}

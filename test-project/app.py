# test-project/app.py
# Phase 5: Added Prometheus /metrics endpoint
#
# Changes from Phase 4B:
#   - Import prometheus_client (add `prometheus-client>=0.20.0` to requirements.txt)
#   - Three metrics exposed:
#       guardops_requests_total       — counter, labelled by path and status_code
#       guardops_request_duration_ms  — gauge,   last request duration per path
#       guardops_app_info             — info,    static build metadata
#   - /metrics route returns Prometheus text format (scraped by kube-prometheus-stack)
#   - All other routes unchanged

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import time
import os

from prometheus_client import (
    Counter,
    Gauge,
    Info,
    generate_latest,
    CONTENT_TYPE_LATEST,
    REGISTRY,
)

# ── Metrics definitions ────────────────────────────────────────────────────────

REQUEST_COUNT = Counter(
    "guardops_requests_total",
    "Total HTTP requests received",
    ["path", "status_code"],
)

REQUEST_DURATION = Gauge(
    "guardops_request_duration_ms",
    "Duration of last HTTP request in milliseconds",
    ["path"],
)

APP_INFO = Info(
    "guardops_app",
    "Static application metadata",
)
APP_INFO.info({
    "version": os.environ.get("APP_VERSION", "unknown"),
    "environment": os.environ.get("ENVIRONMENT", "local"),
})


# ── Request handler ────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        start = time.time()

        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})

        elif self.path == "/ready":
            self._send_json(200, {"ready": True})

        elif self.path == "/metrics":
            # Prometheus scrape endpoint — return text exposition format
            data = generate_latest(REGISTRY)
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            # Don't record /metrics in REQUEST_COUNT — it would pollute graphs
            return

        else:
            self._send_json(200, {
                "message": "GuardOps test app running",
                "path": self.path,
            })

        # Record metrics for every route except /metrics itself
        duration_ms = (time.time() - start) * 1000
        REQUEST_COUNT.labels(path=self.path, status_code="200").inc()
        REQUEST_DURATION.labels(path=self.path).set(duration_ms)

    def _send_json(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass  # silence default request logging


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"GuardOps test server starting on port {port}...")
    print(f"Metrics available at http://0.0.0.0:{port}/metrics")
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

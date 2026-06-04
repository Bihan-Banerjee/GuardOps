"""
tests/test_alertmanager_extra.py — alertmanager_handler uncovered branches.

The real _run_kubectl subprocess wrapper, dispatch routing for resolved + node alerts,
the drain-failure path, and the webhook handler-error response.
"""

import subprocess
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.security import alertmanager_handler as h

_RUN = "backend.security.alertmanager_handler.subprocess.run"
_KUBECTL = "backend.security.alertmanager_handler._run_kubectl"


def _alert(**kw):
    base = dict(alertname="", status="firing", severity="", pod_name="", namespace="",
                rule="", node_name="", summary="", description="", starts_at="", fingerprint="")
    base.update(kw)
    return h.AlertmanagerAlert(**base)


# ── real _run_kubectl ─────────────────────────────────────────────────────────

def test_run_kubectl_success():
    with patch(_RUN, return_value=MagicMock(returncode=0, stdout="ok ", stderr="")):
        ok, out, _ = h._run_kubectl(["get", "pods"])
    assert ok and out == "ok"


def test_run_kubectl_failure():
    with patch(_RUN, return_value=MagicMock(returncode=1, stdout="", stderr="denied ")):
        ok, _, err = h._run_kubectl(["get", "pods"])
    assert not ok and err == "denied"


def test_run_kubectl_timeout():
    with patch(_RUN, side_effect=subprocess.TimeoutExpired(cmd="kubectl", timeout=1)):
        ok, _, err = h._run_kubectl(["get", "pods"])
    assert not ok and "timed out" in err


def test_run_kubectl_not_found():
    with patch(_RUN, side_effect=FileNotFoundError):
        ok, _, err = h._run_kubectl(["get", "pods"])
    assert not ok and "not found" in err


def test_run_kubectl_unexpected_error():
    with patch(_RUN, side_effect=RuntimeError("boom")):
        ok, _, err = h._run_kubectl(["get", "pods"])
    assert not ok and "Unexpected error" in err


# ── dispatch routing via handle_webhook ───────────────────────────────────────

def test_handle_webhook_resolved_cleanup():
    raw = {"alerts": [{"status": "resolved",
                       "labels": {"alertname": "GuardOpsFalcoCritical", "pod": "p1", "namespace": "default"},
                       "fingerprint": "abcd1234"}]}
    with patch(_KUBECTL, return_value=(True, "", "")):
        res = h.handle_webhook(raw)
    assert res.success and res.actions[0].action_type == "resolved"


def test_handle_webhook_node_drain():
    raw = {"alerts": [{"status": "firing",
                       "labels": {"alertname": "NodeMemoryPressure", "node": "n1"},
                       "fingerprint": "ff"}]}
    with patch(_KUBECTL, return_value=(True, "", "")):
        res = h.handle_webhook(raw)
    assert res.success and res.drain_count == 1


# ── drain failure ─────────────────────────────────────────────────────────────

def test_drain_node_drain_step_fails():
    a = _alert(alertname="NodeMemoryPressure", node_name="n1", fingerprint="ff")
    # cordon ok, drain fails
    with patch(_KUBECTL, side_effect=[(True, "", ""), (False, "", "drain timeout")]):
        action = h._drain_node(a)
    assert not action.success and "drain failed" in action.error_message


# ── webhook handler error response ────────────────────────────────────────────

def test_webhook_handler_error_returns_200_with_flag():
    # a JSON array (not a dict) makes _parse_payload raise → success=False, still 200
    r = TestClient(h.app).post("/webhook", json=["not", "a", "dict"])
    assert r.status_code == 200
    assert r.json()["success"] is False

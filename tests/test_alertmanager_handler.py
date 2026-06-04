"""
tests/test_alertmanager_handler.py — backend/security/alertmanager_handler (Phase 8).

The self-healing webhook: parses Alertmanager payloads, routes alerts, and quarantines
pods / drains nodes / cleans up on resolve. Covers the pure logic (parsing, routing,
properties, policy YAML), the kubectl-backed remediation (mocked), and the FastAPI
routes via TestClient. kubectl is never really invoked.
"""

import yaml
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.security.alertmanager_handler import (
    app,
    handle_webhook,
    _parse_payload,
    _dispatch_alert,
    _quarantine_pod,
    _drain_node,
    _handle_resolved,
    _build_quarantine_policy,
    AlertmanagerAlert,
    QuarantineAction,
)

_KUBECTL = "backend.security.alertmanager_handler._run_kubectl"


def _alert(**kw):
    base = dict(alertname="", status="firing", severity="", pod_name="", namespace="",
                rule="", node_name="", summary="", description="", starts_at="", fingerprint="")
    base.update(kw)
    return AlertmanagerAlert(**base)


# ── classification properties ─────────────────────────────────────────────────

def test_is_falco_critical_requires_pod_and_namespace():
    assert _alert(alertname="GuardOpsFalcoCritical", pod_name="p", namespace="default").is_falco_critical
    assert not _alert(alertname="GuardOpsFalcoCritical", namespace="default").is_falco_critical
    assert not _alert(alertname="Other", pod_name="p", namespace="default").is_falco_critical


def test_is_node_pressure_requires_node():
    assert _alert(alertname="NodeMemoryPressure", node_name="n1").is_node_pressure
    assert not _alert(alertname="NodeMemoryPressure").is_node_pressure


def test_is_resolved():
    assert _alert(status="resolved").is_resolved
    assert not _alert(status="firing").is_resolved


# ── parsing ───────────────────────────────────────────────────────────────────

def test_parse_payload_pulls_fields():
    raw = {
        "version": "4", "status": "firing", "receiver": "guardops-webhook", "groupKey": "g",
        "alerts": [{
            "status": "firing",
            "labels": {"alertname": "GuardOpsFalcoCritical", "severity": "critical",
                       "pod": "my-pod", "namespace": "default", "rule": "Shell"},
            "annotations": {"summary": "s"},
            "fingerprint": "abc12345",
        }],
    }
    p = _parse_payload(raw)
    assert p.receiver == "guardops-webhook" and len(p.alerts) == 1
    a = p.alerts[0]
    assert a.pod_name == "my-pod" and a.namespace == "default" and a.fingerprint == "abc12345"
    assert a.is_falco_critical


# ── NetworkPolicy builder ─────────────────────────────────────────────────────

def test_build_quarantine_policy_denies_ingress_allows_dns():
    out = _build_quarantine_policy("default", "guardops-quarantine-abc12345", "abc12345", "Shell Spawned!")
    doc = yaml.safe_load(out)
    assert doc["kind"] == "NetworkPolicy"
    assert doc["spec"]["podSelector"]["matchLabels"]["guardops.io/quarantine"] == "true"
    assert doc["spec"]["ingress"] == []                       # deny all inbound
    dns_ports = doc["spec"]["egress"][0]["ports"]
    assert {"protocol": "UDP", "port": 53} in dns_ports
    assert doc["metadata"]["annotations"]["guardops.io/falco-rule"] == "Shell-Spawned-"  # sanitized


# ── routing ───────────────────────────────────────────────────────────────────

def test_dispatch_routes_falco_critical_to_quarantine():
    a = _alert(alertname="FalcoCritical", pod_name="p", namespace="default", fingerprint="ff")
    with patch("backend.security.alertmanager_handler._quarantine_pod",
               return_value=QuarantineAction("network_policy", True)) as q:
        _dispatch_alert(a)
    q.assert_called_once()


def test_dispatch_skips_unmatched():
    action = _dispatch_alert(_alert(alertname="SomethingElse"))
    assert action.action_type == "skipped" and action.success


# ── remediation: quarantine ───────────────────────────────────────────────────

def test_quarantine_pod_success():
    a = _alert(alertname="GuardOpsFalcoCritical", pod_name="p1", namespace="default", fingerprint="abcd1234ef")
    with patch(_KUBECTL, return_value=(True, "", "")) as mk:
        action = _quarantine_pod(a)
    assert action.success and action.action_type == "network_policy"
    assert action.policy_name == "guardops-quarantine-abcd1234"   # fingerprint[:8]
    assert mk.call_count == 2                                       # label + apply


def test_quarantine_pod_label_failure():
    a = _alert(alertname="GuardOpsFalcoCritical", pod_name="p1", namespace="default", fingerprint="abcd1234")
    with patch(_KUBECTL, return_value=(False, "", "forbidden")):
        action = _quarantine_pod(a)
    assert not action.success and "label" in action.error_message


def test_quarantine_pod_apply_failure():
    a = _alert(alertname="GuardOpsFalcoCritical", pod_name="p1", namespace="default", fingerprint="abcd1234")
    with patch(_KUBECTL, side_effect=[(True, "", ""), (False, "", "apply boom")]):
        action = _quarantine_pod(a)
    assert not action.success and "NetworkPolicy" in action.error_message


# ── remediation: node drain ───────────────────────────────────────────────────

def test_drain_node_success():
    a = _alert(alertname="NodeMemoryPressure", node_name="n1")
    with patch(_KUBECTL, return_value=(True, "", "")) as mk:
        action = _drain_node(a)
    assert action.success and action.action_type == "node_drain" and mk.call_count == 2


def test_drain_node_cordon_failure_skips_drain():
    a = _alert(alertname="NodeMemoryPressure", node_name="n1")
    with patch(_KUBECTL, return_value=(False, "", "cordon denied")) as mk:
        action = _drain_node(a)
    assert not action.success and "cordon" in action.error_message
    assert mk.call_count == 1   # drain not attempted


# ── remediation: resolved cleanup ─────────────────────────────────────────────

def test_handle_resolved_cleans_up():
    a = _alert(status="resolved", pod_name="p1", namespace="default", fingerprint="abcd1234")
    with patch(_KUBECTL, return_value=(True, "", "")) as mk:
        action = _handle_resolved(a)
    assert action.success and action.action_type == "resolved"
    assert mk.call_count == 2   # delete policy + remove label


def test_handle_resolved_without_fingerprint_skips():
    a = _alert(status="resolved", pod_name="p1", namespace="default", fingerprint="")
    with patch(_KUBECTL) as mk:
        action = _handle_resolved(a)
    assert action.success and "cleanup skipped" in action.error_message
    mk.assert_not_called()


# ── handle_webhook (end to end, kubectl mocked) ───────────────────────────────

def test_handle_webhook_quarantines_firing_alert():
    raw = {"alerts": [{"status": "firing",
                       "labels": {"alertname": "GuardOpsFalcoCritical", "pod": "p1", "namespace": "default"},
                       "fingerprint": "abcd1234"}]}
    with patch(_KUBECTL, return_value=(True, "", "")):
        res = handle_webhook(raw)
    assert res.success and res.quarantine_count == 1 and res.failed_count == 0


def test_handle_webhook_parse_error_is_non_fatal():
    res = handle_webhook(["not", "a", "dict"])   # .get() will fail inside parse
    assert not res.success and "parse error" in res.error_message.lower()


# ── FastAPI routes ────────────────────────────────────────────────────────────

def test_healthz():
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_readyz_ready_when_kubectl_ok():
    with patch(_KUBECTL, return_value=(True, "Client Version v1.28", "")):
        r = TestClient(app).get("/readyz")
    assert r.status_code == 200 and r.json()["status"] == "ready"


def test_readyz_503_when_kubectl_unavailable():
    with patch(_KUBECTL, return_value=(False, "", "kubectl not found")):
        r = TestClient(app).get("/readyz")
    assert r.status_code == 503


def test_webhook_post_returns_action_summary():
    payload = {"alerts": [{"status": "firing",
                           "labels": {"alertname": "GuardOpsFalcoCritical", "pod": "p1", "namespace": "default"},
                           "fingerprint": "abcd1234"}]}
    with patch(_KUBECTL, return_value=(True, "", "")):
        r = TestClient(app).post("/webhook", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] and body["quarantine_count"] == 1


def test_webhook_invalid_json_returns_400():
    r = TestClient(app).post("/webhook", content="not json",
                             headers={"Content-Type": "application/json"})
    assert r.status_code == 400

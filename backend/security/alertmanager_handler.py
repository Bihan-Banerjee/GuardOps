"""
backend/security/alertmanager_handler.py

FastAPI webhook receiver for Alertmanager alerts — Phase 8 Self-Healing.

WHY THIS EXISTS:
  Phase 7 gave us visibility — GuardOps can *see* Falco alerts via Loki.
  Phase 8 closes the loop: when Alertmanager fires a CRITICAL alert,
  this service responds automatically instead of waiting for a human.

  Alertmanager sends an HTTP POST to this service whenever a Prometheus
  alert fires or resolves.  This handler inspects each alert and takes the
  appropriate automated remediation action:

    1. CRITICAL Falco alerts  → apply a restrictive NetworkPolicy to quarantine
                                the offending pod (block all ingress/egress
                                except DNS on port 53 UDP+TCP).
    2. Node resource pressure → kubectl cordon + drain the node before EKS
                                auto-healing replaces it via the managed node
                                group's health check.
    3. Resolved alerts        → remove the quarantine NetworkPolicy + label so
                                normal traffic resumes.
    4. Everything else        → log and skip (no action taken).

DESIGN PRINCIPLES (mirrors falco_reader.py / zap_runner.py):
  - Named dataclasses for every result type — no raw dicts returned to callers.
  - success=False + error_message instead of raised exceptions — the FastAPI
    layer can always return a structured JSON response regardless of what broke.
  - All kubectl calls go through _run_kubectl() — mirrors get_command_output()
    in cli/utils/system.py.  subprocess over kubernetes-client SDK to stay
    consistent with builder.py, deployer.py, and keep dependencies minimal.
  - Business logic (handle_webhook, _quarantine_pod, _drain_node) is pure
    functions — easy to unit-test without a live cluster.
  - FastAPI and uvicorn at the bottom — HTTP plumbing is separate from logic.

QUARANTINE MECHANISM:
  Rather than guessing which app label a pod carries (which might match
  sibling replicas), we first *add* a guardops.io/quarantine=true label to
  the specific offending pod, then create a NetworkPolicy that selects on
  exactly that label.  This guarantees pod-level precision regardless of
  how the deployment is labelled.

ALERTMANAGER WEBHOOK PAYLOAD (v4 schema):
  POST /webhook
  Content-Type: application/json
  {
    "version":    "4",
    "status":     "firing" | "resolved",
    "receiver":   "guardops-webhook",
    "groupKey":   "{}:{alertname=\\"GuardOpsFalcoCritical\\"}",
    "alerts": [
      {
        "status":      "firing",
        "labels": {
          "alertname": "GuardOpsFalcoCritical",
          "severity":  "critical",
          "pod":       "my-app-7d9f6b-xk2pq",
          "namespace": "default",
          "rule":      "Shell Spawned Inside Container"
        },
        "annotations": {
          "summary":     "Critical Falco alert in pod my-app-7d9f6b-xk2pq",
          "description": "A shell was spawned inside a container..."
        },
        "startsAt":   "2024-06-15T09:23:11.000Z",
        "endsAt":     "0001-01-01T00:00:00Z",
        "fingerprint": "a1b2c3d4e5f6a7b8"
      }
    ]
  }

RELATED FILES:
  backend/security/falco_reader.py          — Loki query client (Phase 7)
  k8s/alertmanager/quarantine-webhook.yaml  — Alertmanager receiver config (Phase 8)
  k8s/networkpolicy/quarantine-template.yaml — reference NetworkPolicy (Phase 8)
  cli/commands/quarantine_cmd.py            — guardops quarantine-status (Phase 8)
  infra/terraform/modules/alertmanager-webhook/ — K8s Service + Deployment (Phase 8)
"""

import datetime
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

import yaml
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


# ── Logging ───────────────────────────────────────────────────────────────────

# Structured format so Loki/Promtail can parse fields from the handler's own logs.
# The handler's logs are themselves observable via `guardops runtime-status`.
logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("guardops.webhook")


# ── Alert classification constants ────────────────────────────────────────────

# Alertmanager alertname values that indicate a CRITICAL Falco runtime event.
# These must match the alertname in your PrometheusRule / alerting rules.
# The generic "FalcoAlert" entry acts as a catch-all if your rules don't split
# by severity at the alertname level.
FALCO_CRITICAL_ALERTNAMES: frozenset[str] = frozenset({
    "GuardOpsFalcoCritical",   # GuardOps custom rule (Phase 7 custom-rules.yaml)
    "FalcoCritical",           # Falco Helm chart default critical alert
    "FalcoAlert",              # Generic catch-all — matches any Falco alert
})

# Alertname values that indicate a node is under resource pressure and should
# be drained before EKS auto-heals it.  These come from kube-prometheus-stack's
# built-in alert rules.
NODE_PRESSURE_ALERTNAMES: frozenset[str] = frozenset({
    "NodeMemoryPressure",
    "NodeDiskPressure",
    "NodePIDPressure",
    "KubeNodeNotReady",
    "KubeNodeUnreachable",
})

# Label applied to the offending pod before the NetworkPolicy is created.
# Using a dedicated quarantine label (instead of existing app labels) makes
# the NetworkPolicy selector pod-precise — sibling replicas won't be affected.
QUARANTINE_LABEL_KEY   = "guardops.io/quarantine"
QUARANTINE_LABEL_VALUE = "true"

# Prefix for all NetworkPolicy resources created by this handler.
# Full name: guardops-quarantine-<fingerprint[:8]>
# The fingerprint suffix makes each policy unique and lets _handle_resolved
# derive the exact policy name from the alert fingerprint.
POLICY_NAME_PREFIX = "guardops-quarantine"

# Default port the FastAPI server listens on inside the pod.
# Override via WEBHOOK_PORT environment variable.
WEBHOOK_PORT = 9095

# kubectl command timeout in seconds.  Quarantine must be fast — 30 s is
# enough for a label + NetworkPolicy apply against a healthy API server.
KUBECTL_TIMEOUT = 30

# kubectl drain timeout passed as --timeout flag.  Drain can be slow if pods
# have long terminationGracePeriodSeconds; 120 s is a safe default.
DRAIN_TIMEOUT = "120s"


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class AlertmanagerAlert:
    """
    A single alert entry extracted from an Alertmanager webhook payload.

    Alertmanager sends a list of alerts in each POST (grouped by labels).
    We pull the fields relevant to remediation into named attributes and
    preserve the full label set in raw_labels for downstream access.

    The three @property helpers encode the routing logic in one place:
    _dispatch_alert() reads only these properties, not raw labels.
    """
    alertname:       str
    status:          str            # "firing" | "resolved"
    severity:        str            # labels.severity: "critical" | "warning" | etc.
    pod_name:        str            # labels.pod — empty for node-level alerts
    namespace:       str            # labels.namespace
    rule:            str            # labels.rule — Falco rule name if applicable
    node_name:       str            # labels.node — for node drain alerts
    summary:         str            # annotations.summary
    description:     str            # annotations.description
    starts_at:       str            # ISO-8601 timestamp from Alertmanager
    fingerprint:     str            # Alertmanager dedup key — unique per alert series
    raw_labels:      dict = field(default_factory=dict)
    raw_annotations: dict = field(default_factory=dict)

    @property
    def is_falco_critical(self) -> bool:
        """
        True when this alert should trigger pod quarantine.

        Requires both pod_name and namespace so the quarantine action has a
        specific target.  Alerts missing either field are logged and skipped.
        """
        return (
            self.alertname in FALCO_CRITICAL_ALERTNAMES
            and self.status == "firing"
            and bool(self.pod_name)
            and bool(self.namespace)
        )

    @property
    def is_node_pressure(self) -> bool:
        """
        True when this alert should trigger node cordon + drain.

        node_name must be present — without it we don't know which node to act on.
        """
        return (
            self.alertname in NODE_PRESSURE_ALERTNAMES
            and self.status == "firing"
            and bool(self.node_name)
        )

    @property
    def is_resolved(self) -> bool:
        """True when Alertmanager signals that this alert has cleared."""
        return self.status == "resolved"


@dataclass
class AlertmanagerPayload:
    """
    The full JSON body that Alertmanager POSTs to this webhook receiver.

    Alertmanager v4 payload spec:
      https://prometheus.io/docs/alerting/latest/configuration/#webhook_config
    """
    version:   str
    status:    str              # "firing" | "resolved" at group level
    receiver:  str              # Alertmanager receiver name (e.g. "guardops-webhook")
    group_key: str              # Group dedup key
    alerts:    list[AlertmanagerAlert] = field(default_factory=list)
    raw:       dict             = field(default_factory=dict)


@dataclass
class QuarantineAction:
    """
    Result of a single automated remediation action.

    One QuarantineAction is produced per alert processed by handle_webhook().
    action_type records what was attempted; success records whether it worked.
    All fields have defaults so callers can construct partial results on early
    failure without having to supply every field.
    """
    action_type:      str           # "network_policy" | "node_drain" | "resolved" | "skipped"
    success:          bool
    fingerprint:      str   = ""
    pod_name:         str   = ""
    namespace:        str   = ""
    node_name:        str   = ""
    policy_name:      str   = ""    # NetworkPolicy name created or deleted
    alertname:        str   = ""
    error_message:    str   = ""
    duration_seconds: float = 0.0


@dataclass
class WebhookHandlerResult:
    """
    Aggregate result of processing one Alertmanager webhook POST.

    Mirrors the FalcoQueryResult / ZapScanResult interface used throughout
    GuardOps: success=False + error_message instead of raised exceptions,
    so the FastAPI layer can always produce a structured JSON response.
    """
    success:          bool
    alerts_received:  int   = 0
    actions:          list[QuarantineAction] = field(default_factory=list)
    error_message:    str   = ""
    duration_seconds: float = 0.0

    @property
    def quarantine_count(self) -> int:
        """Number of pods successfully quarantined in this request."""
        return sum(
            1 for a in self.actions
            if a.action_type == "network_policy" and a.success
        )

    @property
    def drain_count(self) -> int:
        """Number of nodes successfully drained in this request."""
        return sum(
            1 for a in self.actions
            if a.action_type == "node_drain" and a.success
        )

    @property
    def failed_count(self) -> int:
        """Number of actions that were attempted but failed."""
        return sum(
            1 for a in self.actions
            if not a.success and a.action_type not in ("skipped", "resolved")
        )


# ── Public API ────────────────────────────────────────────────────────────────

def handle_webhook(raw_payload: dict) -> WebhookHandlerResult:
    """
    Main entry point.  Parses an Alertmanager webhook payload and dispatches
    a remediation action for each alert it contains.

    This function is deliberately framework-agnostic — it takes a plain dict
    and returns a dataclass.  The FastAPI route calls it and handles HTTP
    concerns separately.  This makes the business logic unit-testable without
    spinning up an HTTP server.

    Args:
        raw_payload: Decoded JSON body from the Alertmanager POST request.

    Returns:
        WebhookHandlerResult with one QuarantineAction per alert processed.
        Never raises — all errors are captured in the result's error_message.
    """
    start = time.time()

    # ── Parse ─────────────────────────────────────────────────────────────────
    try:
        payload = _parse_payload(raw_payload)
    except Exception as exc:
        logger.error("Failed to parse Alertmanager payload: %s", exc)
        return WebhookHandlerResult(
            success=False,
            error_message=f"Payload parse error: {exc}",
            duration_seconds=time.time() - start,
        )

    logger.info(
        "Webhook received  receiver=%s  status=%s  alerts=%d",
        payload.receiver, payload.status, len(payload.alerts),
    )

    # ── Dispatch ──────────────────────────────────────────────────────────────
    actions: list[QuarantineAction] = []

    for alert in payload.alerts:
        action = _dispatch_alert(alert)
        actions.append(action)

        if action.success:
            logger.info(
                "Action OK  type=%-14s  alert=%-30s  pod=%s  ns=%s  node=%s",
                action.action_type, alert.alertname,
                action.pod_name, action.namespace, action.node_name,
            )
        elif action.action_type not in ("skipped",):
            logger.error(
                "Action FAILED  type=%s  alert=%s  error=%s",
                action.action_type, alert.alertname, action.error_message,
            )

    return WebhookHandlerResult(
        success=True,
        alerts_received=len(payload.alerts),
        actions=actions,
        duration_seconds=time.time() - start,
    )


# ── Internal: parsing ─────────────────────────────────────────────────────────

def _parse_payload(raw: dict) -> AlertmanagerPayload:
    """
    Converts the raw Alertmanager JSON dict into a typed AlertmanagerPayload.

    Uses .get() throughout so unexpected payload shapes produce empty-string
    defaults rather than KeyError crashes — the routing properties on
    AlertmanagerAlert handle the "required field missing" case gracefully
    by returning False for is_falco_critical / is_node_pressure.
    """
    alerts = [_parse_alert(a) for a in raw.get("alerts", [])]
    return AlertmanagerPayload(
        version   = str(raw.get("version", "4")),
        status    = raw.get("status", ""),
        receiver  = raw.get("receiver", ""),
        group_key = raw.get("groupKey", ""),
        alerts    = alerts,
        raw       = raw,
    )


def _parse_alert(alert_dict: dict) -> AlertmanagerAlert:
    """
    Parses one entry from the Alertmanager payload's 'alerts' list.

    Label names depend on how the PrometheusRule is written.  We try the
    canonical name first, then common aliases, then fall back to empty string.
    The caller's routing properties will return False when required fields are
    empty, so the skipped action path handles the fallback cleanly.
    """
    labels      = alert_dict.get("labels", {})
    annotations = alert_dict.get("annotations", {})

    return AlertmanagerAlert(
        alertname   = labels.get("alertname", ""),
        status      = alert_dict.get("status", ""),
        severity    = labels.get("severity", ""),
        # pod_name: Alertmanager uses "pod" by convention; some rules use "pod_name"
        pod_name    = labels.get("pod", labels.get("pod_name", "")),
        namespace   = labels.get("namespace", labels.get("exported_namespace", "")),
        rule        = labels.get("rule", labels.get("falco_rule", "")),
        node_name   = labels.get("node", labels.get("node_name", "")),
        summary     = annotations.get("summary", ""),
        description = annotations.get("description", ""),
        starts_at   = alert_dict.get("startsAt", ""),
        fingerprint = alert_dict.get("fingerprint", ""),
        raw_labels      = labels,
        raw_annotations = annotations,
    )


# ── Internal: dispatch ────────────────────────────────────────────────────────

def _dispatch_alert(alert: AlertmanagerAlert) -> QuarantineAction:
    """
    Routes a single alert to the appropriate remediation handler.

    Priority order matters: we check is_resolved first so a resolving CRITICAL
    Falco alert goes through cleanup rather than accidentally triggering a
    second quarantine.

    Routing:
      1. Resolved alerts     → _handle_resolved   (clean up quarantine artefacts)
      2. CRITICAL Falco      → _quarantine_pod     (label pod + apply NetworkPolicy)
      3. Node pressure       → _drain_node         (kubectl cordon + drain)
      4. Everything else     → skip                (log, no action)
    """
    if alert.is_resolved:
        return _handle_resolved(alert)

    if alert.is_falco_critical:
        return _quarantine_pod(alert)

    if alert.is_node_pressure:
        return _drain_node(alert)

    logger.info(
        "Skipping  alertname=%s  status=%s  pod=%s  (no handler matched)",
        alert.alertname, alert.status, alert.pod_name,
    )
    return QuarantineAction(
        action_type = "skipped",
        success     = True,
        alertname   = alert.alertname,
        fingerprint = alert.fingerprint,
        pod_name    = alert.pod_name,
        namespace   = alert.namespace,
    )


# ── Internal: remediation ─────────────────────────────────────────────────────

def _quarantine_pod(alert: AlertmanagerAlert) -> QuarantineAction:
    """
    Quarantines the pod identified in a CRITICAL Falco alert.

    Two-step process:

      Step 1 — Label the pod.
        We add guardops.io/quarantine=true to the specific offending pod.
        This is more precise than using app labels which would also match
        healthy sibling replicas in the same ReplicaSet.

      Step 2 — Apply a NetworkPolicy.
        The policy selects pods with guardops.io/quarantine=true and:
          - Blocks ALL ingress (empty ingress list = deny all).
          - Allows egress ONLY to port 53 UDP+TCP (DNS resolution).
        Allowing DNS means the pod can still resolve names for logging/telemetry
        but cannot make arbitrary outbound connections to exfiltrate data.

    WHY subprocess over the kubernetes Python client?
      Consistent with builder.py and deployer.py.  kubectl is always present
      in the handler pod image.  Avoids a heavyweight SDK dependency and keeps
      the auth model simple (in-cluster service account via KUBECONFIG env
      or /var/run/secrets/kubernetes.io/serviceaccount/).

    Args:
        alert: A Falco CRITICAL alert with non-empty pod_name and namespace.

    Returns:
        QuarantineAction with success=True on full completion,
        success=False if either the label or NetworkPolicy step fails.
    """
    start       = time.time()
    pod_name    = alert.pod_name
    namespace   = alert.namespace
    fp_short    = alert.fingerprint[:8] if alert.fingerprint else "nofp"
    policy_name = f"{POLICY_NAME_PREFIX}-{fp_short}"

    logger.warning(
        "QUARANTINE  pod=%s  ns=%s  rule=%s  fingerprint=%s",
        pod_name, namespace, alert.rule, alert.fingerprint,
    )

    # ── Step 1: Label the pod ─────────────────────────────────────────────────
    label_ok, _, label_err = _run_kubectl([
        "label", "pod", pod_name,
        f"{QUARANTINE_LABEL_KEY}={QUARANTINE_LABEL_VALUE}",
        "--overwrite",
        "-n", namespace,
    ])

    if not label_ok:
        return QuarantineAction(
            action_type      = "network_policy",
            success          = False,
            alertname        = alert.alertname,
            fingerprint      = alert.fingerprint,
            pod_name         = pod_name,
            namespace        = namespace,
            policy_name      = policy_name,
            error_message    = f"kubectl label pod failed: {label_err}",
            duration_seconds = time.time() - start,
        )

    logger.info("Pod labelled  pod=%s  ns=%s", pod_name, namespace)

    # ── Step 2: Apply NetworkPolicy ───────────────────────────────────────────
    policy_yaml = _build_quarantine_policy(
        namespace   = namespace,
        policy_name = policy_name,
        fingerprint = alert.fingerprint,
        rule        = alert.rule,
    )

    policy_ok, _, policy_err = _run_kubectl(
        ["apply", "-f", "-"],
        input_text=policy_yaml,
    )

    if not policy_ok:
        return QuarantineAction(
            action_type      = "network_policy",
            success          = False,
            alertname        = alert.alertname,
            fingerprint      = alert.fingerprint,
            pod_name         = pod_name,
            namespace        = namespace,
            policy_name      = policy_name,
            error_message    = f"kubectl apply NetworkPolicy failed: {policy_err}",
            duration_seconds = time.time() - start,
        )

    logger.warning(
        "Pod QUARANTINED  pod=%s  ns=%s  policy=%s",
        pod_name, namespace, policy_name,
    )

    return QuarantineAction(
        action_type      = "network_policy",
        success          = True,
        alertname        = alert.alertname,
        fingerprint      = alert.fingerprint,
        pod_name         = pod_name,
        namespace        = namespace,
        policy_name      = policy_name,
        duration_seconds = time.time() - start,
    )


def _drain_node(alert: AlertmanagerAlert) -> QuarantineAction:
    """
    Cordons then drains the node identified in a node-pressure alert.

    WHY cordon before drain?
      Cordon marks the node unschedulable immediately so the scheduler won't
      place new pods on it during the drain window.  Without cordon, a pod
      evicted from the node could be rescheduled right back onto it.

    kubectl drain flags:
      --ignore-daemonsets     DaemonSet pods (Falco, Promtail, node-exporter)
                              are managed by their controller and will respawn
                              on another node — we can't remove them, so ignore.
      --delete-emulated-pods  Removes mirror/static pods emulated by kubelet.
      --force                 Evicts pods not owned by a ReplicationController,
                              ReplicaSet, Job, DaemonSet, or StatefulSet.
      --grace-period=30       Gives pods 30 s to shut down before SIGKILL.
      --timeout=120s          Abort if drain doesn't complete in 2 min;
                              EKS will replace the node regardless.

    EKS auto-healing:
      After drain, the managed node group's health check detects the node is
      NotReady/cordoned and automatically provisions a replacement.

    Args:
        alert: A node-pressure alert with a non-empty node_name.

    Returns:
        QuarantineAction with success=True if both cordon and drain succeed.
        Returns success=False at the first failure; drain is not attempted if
        cordon fails because the node may still accept new pods.
    """
    start     = time.time()
    node_name = alert.node_name

    logger.warning(
        "NODE DRAIN  node=%s  alert=%s",
        node_name, alert.alertname,
    )

    # ── Cordon ────────────────────────────────────────────────────────────────
    cordon_ok, _, cordon_err = _run_kubectl(["cordon", node_name])

    if not cordon_ok:
        return QuarantineAction(
            action_type      = "node_drain",
            success          = False,
            alertname        = alert.alertname,
            fingerprint      = alert.fingerprint,
            node_name        = node_name,
            error_message    = f"kubectl cordon failed: {cordon_err}",
            duration_seconds = time.time() - start,
        )

    logger.info("Node cordoned  node=%s", node_name)

    # ── Drain ─────────────────────────────────────────────────────────────────
    drain_ok, _, drain_err = _run_kubectl(
        [
            "drain", node_name,
            "--ignore-daemonsets",
            "--delete-emulated-pods",
            "--force",
            "--grace-period=30",
            f"--timeout={DRAIN_TIMEOUT}",
        ],
        timeout=150,    # subprocess timeout slightly above --timeout so kubectl
    )               # can surface its own error message before we kill it.

    if not drain_ok:
        return QuarantineAction(
            action_type      = "node_drain",
            success          = False,
            alertname        = alert.alertname,
            fingerprint      = alert.fingerprint,
            node_name        = node_name,
            error_message    = f"kubectl drain failed: {drain_err}",
            duration_seconds = time.time() - start,
        )

    logger.warning(
        "Node DRAINED  node=%s  — EKS will replace this node automatically",
        node_name,
    )

    return QuarantineAction(
        action_type      = "node_drain",
        success          = True,
        alertname        = alert.alertname,
        fingerprint      = alert.fingerprint,
        node_name        = node_name,
        duration_seconds = time.time() - start,
    )


def _handle_resolved(alert: AlertmanagerAlert) -> QuarantineAction:
    """
    Cleans up quarantine artefacts when Alertmanager signals an alert resolved.

    Removes the quarantine NetworkPolicy and the guardops.io/quarantine label
    from the pod so normal traffic resumes.  Both operations are idempotent:
    --ignore-not-found means they succeed even if the pod was evicted or the
    policy was never created (e.g. the alert was skipped on the way in).

    Policy name derivation:
      The same fingerprint[:8] used in _quarantine_pod is used here, so
      _handle_resolved can find the exact policy without querying the API.
      If the fingerprint is absent we skip cleanup to avoid deleting the
      wrong policy — this is logged as a warning, not an error.

    Args:
        alert: A resolved alert, potentially with pod_name and namespace.

    Returns:
        QuarantineAction with action_type="resolved". Always success=True
        (cleanup failure is logged but not treated as a fatal error — the
        pod may already be gone).
    """
    start     = time.time()
    pod_name  = alert.pod_name
    namespace = alert.namespace
    fp_short  = alert.fingerprint[:8] if alert.fingerprint else ""

    logger.info(
        "RESOLVED  alert=%s  pod=%s  ns=%s  fingerprint=%s",
        alert.alertname, pod_name, namespace, alert.fingerprint,
    )

    if not fp_short:
        logger.warning(
            "No fingerprint on resolved alert %s — skipping quarantine cleanup",
            alert.alertname,
        )
        return QuarantineAction(
            action_type      = "resolved",
            success          = True,
            alertname        = alert.alertname,
            fingerprint      = alert.fingerprint,
            pod_name         = pod_name,
            namespace        = namespace,
            error_message    = "No fingerprint — quarantine cleanup skipped",
            duration_seconds = time.time() - start,
        )

    policy_name = f"{POLICY_NAME_PREFIX}-{fp_short}"

    # Delete NetworkPolicy — safe if it never existed or was already deleted.
    _run_kubectl([
        "delete", "networkpolicy", policy_name,
        "-n", namespace,
        "--ignore-not-found",
    ])

    # Remove quarantine label from the pod.
    # Trailing dash on a label key removes it: "guardops.io/quarantine-"
    if pod_name and namespace:
        _run_kubectl([
            "label", "pod", pod_name,
            f"{QUARANTINE_LABEL_KEY}-",
            "-n", namespace,
            "--ignore-not-found",
        ])

    logger.info(
        "Quarantine LIFTED  pod=%s  ns=%s  policy=%s",
        pod_name, namespace, policy_name,
    )

    return QuarantineAction(
        action_type      = "resolved",
        success          = True,
        alertname        = alert.alertname,
        fingerprint      = alert.fingerprint,
        pod_name         = pod_name,
        namespace        = namespace,
        policy_name      = policy_name,
        duration_seconds = time.time() - start,
    )


# ── Internal: NetworkPolicy builder ──────────────────────────────────────────

def _build_quarantine_policy(
    namespace:   str,
    policy_name: str,
    fingerprint: str,
    rule:        str,
) -> str:
    """
    Generates the YAML for a quarantine NetworkPolicy as a string.

    The policy selects pods with guardops.io/quarantine=true and:
      - Blocks ALL ingress   (empty ingress list = implicit deny-all).
      - Allows egress ONLY to port 53 UDP+TCP so the pod can still resolve
        DNS (needed by logging/telemetry agents that may be running inside
        the same container).

    Why yaml.dump() over a hardcoded YAML string?
      dict → yaml.dump() is safer: no indentation bugs, special characters
      in the fingerprint or rule name are automatically escaped, and the
      structure is easy to extend (e.g. allow egress to Loki for log drain).

    Kubernetes label value constraints: [a-zA-Z0-9._-], max 63 chars.
    The rule name is sanitized before use as a label/annotation value.

    Args:
        namespace:   Namespace to create the policy in.
        policy_name: Unique name for this NetworkPolicy resource.
        fingerprint: Alertmanager fingerprint — stored as a label for tracing.
        rule:        Falco rule name — stored in annotation for ops context.

    Returns:
        YAML string suitable for piping to `kubectl apply -f -`.
    """
    # Sanitize the Falco rule name for K8s label/annotation constraints.
    safe_rule = re.sub(r"[^a-zA-Z0-9._-]", "-", rule)[:63]
    quarantined_at = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")

    policy = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name":      policy_name,
            "namespace": namespace,
            "labels": {
                "guardops.io/managed-by":  "alertmanager-handler",
                "guardops.io/quarantine":  "true",
                # Truncate to 63 chars — K8s label value limit.
                "guardops.io/fingerprint": fingerprint[:63],
            },
            "annotations": {
                "guardops.io/quarantined-at": quarantined_at,
                "guardops.io/falco-rule":     safe_rule,
                "guardops.io/reason": (
                    "automatic-quarantine-on-critical-falco-alert"
                ),
            },
        },
        "spec": {
            "podSelector": {
                "matchLabels": {
                    QUARANTINE_LABEL_KEY: QUARANTINE_LABEL_VALUE,
                }
            },
            "policyTypes": ["Ingress", "Egress"],
            # ingress: [] → deny all inbound traffic.
            # An absent ingress key would mean "allow all"; explicit empty list
            # means "deny all" per the NetworkPolicy spec.
            "ingress": [],
            # egress: allow DNS only.
            # Pods still need to resolve names for logging agents and health
            # checks.  All other outbound connections are blocked.
            "egress": [
                {
                    "ports": [
                        {"protocol": "UDP", "port": 53},
                        {"protocol": "TCP", "port": 53},
                    ]
                }
            ],
        },
    }

    return yaml.dump(
        policy,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        indent=2,
    )


# ── Internal: subprocess wrapper ──────────────────────────────────────────────

def _run_kubectl(
    args:       list[str],
    input_text: Optional[str] = None,
    timeout:    int           = KUBECTL_TIMEOUT,
) -> tuple[bool, str, str]:
    """
    Runs a kubectl command and returns (success, stdout, stderr).

    Mirrors the get_command_output() pattern from cli/utils/system.py:
    never raise, always return a structured (ok, stdout, stderr) tuple.
    The caller decides what to do with a failure.

    Args:
        args:       kubectl subcommand + arguments.
                    e.g. ["label", "pod", "my-pod", "key=value", "-n", "default"]
        input_text: If set, piped to kubectl's stdin.
                    Used for `kubectl apply -f -` (YAML piped in).
        timeout:    Max seconds before the subprocess is killed.

    Returns:
        (success, stdout, stderr) where success = returncode == 0.
    """
    cmd = ["kubectl"] + args
    logger.debug("kubectl %s", " ".join(str(a) for a in args))

    try:
        result = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        success = result.returncode == 0

        if not success:
            logger.debug(
                "kubectl exit %d  stderr=%s",
                result.returncode, result.stderr.strip()[:300],
            )

        return success, result.stdout.strip(), result.stderr.strip()

    except subprocess.TimeoutExpired:
        msg = f"kubectl {args[0] if args else '?'} timed out after {timeout}s"
        logger.error(msg)
        return False, "", msg

    except FileNotFoundError:
        msg = "kubectl not found — is it installed and on PATH inside the handler pod?"
        logger.error(msg)
        return False, "", msg

    except Exception as exc:
        msg = f"Unexpected error running kubectl: {exc}"
        logger.error(msg)
        return False, "", msg


# ── FastAPI application ───────────────────────────────────────────────────────

app = FastAPI(
    title       = "GuardOps Alertmanager Webhook",
    description = (
        "Receives Alertmanager webhook POSTs and applies automated "
        "self-healing: pod quarantine via NetworkPolicy for CRITICAL Falco "
        "alerts, and node cordon + drain for node-pressure alerts. "
        "Phase 8 — GuardOps v0.8.0."
    ),
    version = "0.8.0",
)


@app.get("/healthz")
async def healthz() -> dict:
    """
    Liveness probe.

    Returns 200 immediately — if FastAPI is serving requests, the handler
    process is alive.  K8s will restart the pod if this stops responding.
    """
    return {"status": "ok", "version": "0.8.0"}


@app.get("/readyz")
async def readyz() -> dict:
    """
    Readiness probe.

    Checks that kubectl is reachable and the in-cluster service account is
    working.  K8s removes the pod from the Service endpoints if this fails,
    so Alertmanager won't send webhooks to a handler that can't act on them.
    """
    ok, stdout, stderr = _run_kubectl(["version", "--client", "--short"])
    if not ok:
        raise HTTPException(
            status_code=503,
            detail=f"kubectl unavailable: {stderr}",
        )
    return {"status": "ready", "kubectl": stdout}


@app.post("/webhook")
@app.post("/")
async def receive_webhook(request: Request) -> JSONResponse:
    """
    Main webhook endpoint.

    Alertmanager POSTs here on every alert state change (firing and resolved).
    It retries with exponential backoff on non-2xx responses — so we always
    return 200 even on partial handler failure.  Returning 5xx would cause
    Alertmanager to retry the same payload indefinitely, potentially re-running
    a quarantine action multiple times.  Errors are fully captured in the JSON
    body for observability.

    The one exception: a 400 is returned for malformed JSON.  A bad payload
    won't improve on retry, and we want to distinguish "bad request" from
    "handler error" in Alertmanager's logs.

    Returns:
        JSONResponse 200 with body describing actions taken and any errors.
    """
    try:
        raw_payload = await request.json()
    except Exception as exc:
        logger.error("Request body is not valid JSON: %s", exc)
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}")

    result = handle_webhook(raw_payload)

    body = {
        "success":           result.success,
        "alerts_received":   result.alerts_received,
        "quarantine_count":  result.quarantine_count,
        "drain_count":       result.drain_count,
        "failed_count":      result.failed_count,
        "duration_seconds":  round(result.duration_seconds, 3),
        "actions": [
            {
                "action_type":      a.action_type,
                "success":          a.success,
                "alertname":        a.alertname,
                "pod_name":         a.pod_name,
                "namespace":        a.namespace,
                "node_name":        a.node_name,
                "policy_name":      a.policy_name,
                "fingerprint":      a.fingerprint,
                "error_message":    a.error_message,
                "duration_seconds": round(a.duration_seconds, 3),
            }
            for a in result.actions
        ],
    }

    if not result.success:
        body["error_message"] = result.error_message
        logger.error("Webhook handler error: %s", result.error_message)

    return JSONResponse(content=body, status_code=200)


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("WEBHOOK_PORT", WEBHOOK_PORT))
    logger.info(
        "Starting GuardOps Alertmanager webhook handler  port=%d", port
    )
    uvicorn.run(
        "backend.security.alertmanager_handler:app",
        host      = "0.0.0.0",
        port      = port,
        log_level = "info",
        access_log = True,
    )

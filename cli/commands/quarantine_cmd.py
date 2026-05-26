"""
cli/commands/quarantine_cmd.py

`guardops quarantine-status` — Phase 8 Self-Healing.

Phase 9 changes:
  - Added --env flag so operators working in a specific environment (staging/prod)
    get the correct default namespace without having to pass --namespace explicitly.
    Namespace resolution order:
      1. --namespace flag (explicit always wins)
      2. --env flag       (derives namespace from environments.<env>.kubernetes.namespace)
      3. kubernetes.namespace from .guardops.yaml
      4. "default"
  - Imported get_env_config, resolve_namespace from config.py

WHY THIS COMMAND EXISTS:
  Phase 8 adds automated pod quarantine: when a CRITICAL Falco alert fires,
  alertmanager_handler.py labels the offending pod and applies a restrictive
  NetworkPolicy that blocks all traffic except DNS.

  This command gives operators visibility into that automated response:
    - Which pods are currently quarantined?
    - What NetworkPolicies are active?
    - How long has a pod been quarantined?
    - Quick kubectl commands to release a pod manually.

WHAT IT QUERIES:
  1. NetworkPolicies with label  guardops.io/managed-by=guardops
  2. Pods with label             guardops.io/quarantine=true

RELATED FILES:
  backend/security/alertmanager_handler.py  — creates the quarantine artefacts
  k8s/networkpolicy/quarantine-template.yaml — reference NetworkPolicy
  k8s/alertmanager/quarantine-webhook.yaml  — Alertmanager receiver config
  cli/utils/config.py                        — loads .guardops.yaml
  cli/utils/output.py                        — Rich console helpers
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from typing import Optional

import click
from rich.table import Table
from rich import box

from cli.utils.config import load_config, merge_with_defaults, resolve_namespace
from cli.utils.output import (
    console,
    header,
    info,
    success,
    warn,
    error,
    blank,
    section,
)


# ── Constants ─────────────────────────────────────────────────────────────────

MANAGED_BY_SELECTOR   = "guardops.io/managed-by=guardops"
QUARANTINE_LABEL      = "guardops.io/quarantine=true"

ANN_FINGERPRINT       = "guardops.io/alert-fingerprint"
ANN_FALCO_RULE        = "guardops.io/falco-rule"
ANN_REASON            = "guardops.io/quarantine-reason"

SEVERITY_STYLES: dict[str, str] = {
    "CRITICAL": "bold red",
    "HIGH":     "bold yellow",
    "MEDIUM":   "yellow",
    "LOW":      "dim",
}

KUBECTL_TIMEOUT = 30


# ── Click command ─────────────────────────────────────────────────────────────

@click.command("quarantine-status")
@click.option(
    "--namespace", "-n",
    default=None,
    help=(
        "Kubernetes namespace to check. "
        "Defaults to the namespace for --env (or kubernetes.namespace in config). "
        "Pass 'all' or use --all-namespaces to check every namespace."
    ),
)
@click.option(
    "--all-namespaces", "-A",
    is_flag=True,
    default=False,
    help="Check all namespaces (equivalent to kubectl -A).",
)
@click.option(
    "--env",
    default=None,
    type=click.Choice(["local", "staging", "prod"], case_sensitive=False),
    help=(
        "Environment to inspect. When set and --namespace is omitted, "
        "the namespace for this environment is used as the default. "
        "Has no effect when --all-namespaces is set."
    ),
)
@click.option(
    "--release",
    metavar="POD_NAME",
    default=None,
    help=(
        "Manually release a quarantined pod by name. "
        "Deletes its NetworkPolicy and removes the quarantine label. "
        "Requires a resolvable namespace (via --namespace, --env, or config)."
    ),
)
@click.option(
    "--json-output", "json_output",
    is_flag=True,
    default=False,
    help="Print raw JSON of quarantined pods and policies (useful for CI/scripts).",
)
def quarantine_status_cmd(
    namespace:      Optional[str],
    all_namespaces: bool,
    env:            Optional[str],
    release:        Optional[str],
    json_output:    bool,
) -> None:
    """
    Show pods currently quarantined by the GuardOps self-healing system.

    Displays active NetworkPolicies applied by the Alertmanager webhook handler
    and the pods they are isolating. Use --release to manually dequarantine a
    specific pod once you have investigated the incident.

    \b
    Examples:
      guardops quarantine-status
      guardops quarantine-status -A
      guardops quarantine-status --env staging
      guardops quarantine-status --namespace default
      guardops quarantine-status --release my-app-7d9f6b-xk2pq --namespace default
      guardops quarantine-status --json-output
    """
    config = load_config()
    config = merge_with_defaults(config)

    if not json_output:
        header(
            "GuardOps · Quarantine Status",
            "Phase 8 — Self-Healing  |  Active quarantine policies and isolated pods",
        )

    # ── Resolve target namespace ──────────────────────────────────────────────
    # Priority: --all-namespaces > --namespace (explicit) > --env > config default
    if all_namespaces or namespace == "all":
        ns_flag    = ["--all-namespaces"]
        ns_display = "all namespaces"
    else:
        if namespace:
            # Explicit --namespace wins over everything.
            resolved_ns = namespace
        elif env:
            # --env without --namespace: derive namespace from environment config.
            resolved_ns = resolve_namespace(config, env)
        else:
            # Neither flag: fall back to base config kubernetes.namespace.
            resolved_ns = config.get("kubernetes", {}).get("namespace", "default")

        ns_flag     = ["-n", resolved_ns]
        ns_display  = resolved_ns

    if not json_output:
        if env and not all_namespaces:
            info(f"Checking namespace(s): [bold]{ns_display}[/bold] (env=[cyan]{env}[/cyan])")
        else:
            info(f"Checking namespace(s): [bold]{ns_display}[/bold]")
        blank()

    # ── Manual release path ───────────────────────────────────────────────────
    if release:
        if all_namespaces or namespace == "all":
            error("--release requires a specific namespace. Use --namespace or --env.")
            sys.exit(1)
        # resolved_ns is set in all non-all-namespaces branches above.
        release_ns = namespace or resolve_namespace(config, env) if env else config.get("kubernetes", {}).get("namespace", "default")
        _release_pod(pod_name=release, namespace=release_ns)
        return

    # ── Query quarantined pods ────────────────────────────────────────────────
    pods = _get_quarantined_pods(ns_flag)

    # ── Query active quarantine NetworkPolicies ───────────────────────────────
    policies = _get_quarantine_policies(ns_flag)

    # ── JSON output mode ──────────────────────────────────────────────────────
    if json_output:
        output = {
            "quarantined_pods":        pods,
            "quarantine_policies":     policies,
            "total_quarantined_pods":  len(pods),
            "total_active_policies":   len(policies),
        }
        click.echo(json.dumps(output, indent=2))
        return

    # ── Nothing quarantined ───────────────────────────────────────────────────
    if not pods and not policies:
        success("No quarantined pods or active quarantine policies found.")
        info(
            "The self-healing system is idle. "
            "CRITICAL Falco alerts will trigger automatic quarantine."
        )
        blank()
        return

    # ── Quarantined pods table ─────────────────────────────────────────────────
    if pods:
        section(f"Quarantined Pods  ({len(pods)})")
        blank()
        _print_pods_table(pods)

    # ── Active NetworkPolicies table ──────────────────────────────────────────
    if policies:
        section(f"Active Quarantine NetworkPolicies  ({len(policies)})")
        blank()
        _print_policies_table(policies)

    # ── Release hint ─────────────────────────────────────────────────────────
    if pods:
        blank()
        info(
            "To manually release a pod after investigation:\n"
            "   [bold green]guardops quarantine-status --release <pod-name> --namespace <ns>[/bold green]"
        )
        blank()


# ── Data fetching ─────────────────────────────────────────────────────────────

def _get_quarantined_pods(ns_flag: list[str]) -> list[dict]:
    """
    Returns a list of pod summary dicts for pods with the quarantine label.

    Each dict contains:
      name, namespace, node, status, age, quarantine_label
    """
    ok, stdout, stderr = _run_kubectl([
        "get", "pods",
        "-l", QUARANTINE_LABEL,
        "-o", "json",
    ] + ns_flag)

    if not ok:
        if "No resources found" in stderr or (not stdout):
            return []
        warn(f"Could not list quarantined pods: {stderr}")
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        warn("Could not parse kubectl pod JSON output.")
        return []

    results = []
    for item in data.get("items", []):
        meta   = item.get("metadata", {})
        spec   = item.get("spec", {})
        status = item.get("status", {})

        creation_ts      = meta.get("creationTimestamp", "")
        quarantine_since = meta.get("labels", {}).get("guardops.io/quarantine-since", "")

        results.append({
            "name":             meta.get("name", "unknown"),
            "namespace":        meta.get("namespace", "unknown"),
            "node":             spec.get("nodeName", "unknown"),
            "phase":            status.get("phase", "Unknown"),
            "created":          creation_ts,
            "quarantine_since": quarantine_since,
            "annotations":      meta.get("annotations", {}),
        })

    return results


def _get_quarantine_policies(ns_flag: list[str]) -> list[dict]:
    """
    Returns a list of NetworkPolicy summary dicts for all active quarantine policies.

    Each dict contains:
      name, namespace, fingerprint, falco_rule, created
    """
    ok, stdout, stderr = _run_kubectl([
        "get", "networkpolicy",
        "-l", MANAGED_BY_SELECTOR,
        "-o", "json",
    ] + ns_flag)

    if not ok:
        if "No resources found" in stderr or (not stdout):
            return []
        warn(f"Could not list quarantine NetworkPolicies: {stderr}")
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        warn("Could not parse kubectl NetworkPolicy JSON output.")
        return []

    results = []
    for item in data.get("items", []):
        meta        = item.get("metadata", {})
        annotations = meta.get("annotations", {})

        results.append({
            "name":        meta.get("name", "unknown"),
            "namespace":   meta.get("namespace", "unknown"),
            "fingerprint": annotations.get(ANN_FINGERPRINT, "-"),
            "falco_rule":  annotations.get(ANN_FALCO_RULE, "-"),
            "reason":      annotations.get(ANN_REASON, "-"),
            "created":     meta.get("creationTimestamp", ""),
        })

    return results


# ── Display helpers ───────────────────────────────────────────────────────────

def _print_pods_table(pods: list[dict]) -> None:
    """Renders quarantined pods as a Rich table."""
    table = Table(
        box=box.ROUNDED,
        border_style="red",
        show_header=True,
        header_style="bold",
        padding=(0, 1),
    )

    table.add_column("Pod Name",  style="bold white",  min_width=30)
    table.add_column("Namespace", style="cyan",         min_width=12)
    table.add_column("Phase",     justify="center",     min_width=10)
    table.add_column("Node",      style="dim",          min_width=20)
    table.add_column("Age",       justify="right",      min_width=8)

    for pod in pods:
        phase       = pod.get("phase", "Unknown")
        phase_style = "green" if phase == "Running" else "yellow"

        table.add_row(
            f"[bold red]🔒 {pod['name']}[/bold red]",
            pod["namespace"],
            f"[{phase_style}]{phase}[/{phase_style}]",
            pod["node"],
            _age_str(pod.get("created", "")),
        )

    console.print(table)
    blank()


def _print_policies_table(policies: list[dict]) -> None:
    """Renders active quarantine NetworkPolicies as a Rich table."""
    table = Table(
        box=box.ROUNDED,
        border_style="yellow",
        show_header=True,
        header_style="bold",
        padding=(0, 1),
    )

    table.add_column("Policy Name",  style="bold white", min_width=36)
    table.add_column("Namespace",    style="cyan",        min_width=12)
    table.add_column("Falco Rule",   min_width=35)
    table.add_column("Fingerprint",  style="dim",         min_width=18)
    table.add_column("Age",          justify="right",     min_width=8)

    for policy in policies:
        fp = policy["fingerprint"]
        table.add_row(
            policy["name"],
            policy["namespace"],
            policy["falco_rule"],
            fp[:16] if len(fp) > 16 else fp,
            _age_str(policy.get("created", "")),
        )

    console.print(table)
    blank()


# ── Manual release ─────────────────────────────────────────────────────────────

def _release_pod(pod_name: str, namespace: str) -> None:
    """
    Manually releases a quarantined pod.

    Mirrors the _handle_resolved() logic in alertmanager_handler.py:
      1. Find and delete the associated NetworkPolicy.
      2. Remove the guardops.io/quarantine label from the pod.
    """
    section(f"Releasing pod: {pod_name}")
    blank()

    ok, stdout, _ = _run_kubectl([
        "get", "networkpolicy",
        "-l", MANAGED_BY_SELECTOR,
        "-n", namespace,
        "-o", "jsonpath={.items[*].metadata.name}",
    ])

    deleted_policies = 0
    if ok and stdout.strip():
        policy_names = stdout.strip().split()
        for policy_name in policy_names:
            del_ok, _, del_err = _run_kubectl([
                "delete", "networkpolicy", policy_name,
                "-n", namespace,
            ])
            if del_ok:
                success(f"Deleted NetworkPolicy: [bold]{policy_name}[/bold]  (namespace: {namespace})")
                deleted_policies += 1
            else:
                warn(f"Could not delete NetworkPolicy {policy_name}: {del_err}")
    else:
        info("No quarantine NetworkPolicies found in this namespace.")

    label_ok, _, label_err = _run_kubectl([
        "label", "pod", pod_name,
        "guardops.io/quarantine-",
        "-n", namespace,
    ])

    if label_ok:
        success(f"Removed quarantine label from pod: [bold]{pod_name}[/bold]")
    else:
        if "not found" in label_err.lower():
            warn(f"Pod {pod_name} not found in namespace {namespace}.")
        else:
            error(f"Could not remove quarantine label from pod: {label_err}")
            sys.exit(1)

    blank()
    success(
        f"Pod [bold]{pod_name}[/bold] released. "
        f"Deleted {deleted_policies} NetworkPolicy(ies)."
    )
    warn(
        "Ensure you have investigated the Falco alert before releasing. "
        "Run [bold]guardops runtime-status --since 1h[/bold] to review recent alerts."
    )
    blank()


# ── subprocess wrapper ─────────────────────────────────────────────────────────

def _run_kubectl(args: list[str]) -> tuple[bool, str, str]:
    """
    Runs a kubectl command and returns (success, stdout, stderr).
    Never raises — always returns a structured tuple.
    """
    cmd = ["kubectl"] + args

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=KUBECTL_TIMEOUT,
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()

    except subprocess.TimeoutExpired:
        msg = f"kubectl {args[0] if args else '?'} timed out after {KUBECTL_TIMEOUT}s"
        return False, "", msg

    except FileNotFoundError:
        return False, "", "kubectl not found — is it installed and on PATH?"

    except Exception as exc:
        return False, "", f"Unexpected error running kubectl: {exc}"


# ── Utility ───────────────────────────────────────────────────────────────────

def _age_str(timestamp: str) -> str:
    """
    Converts a Kubernetes creationTimestamp (ISO-8601) to a human-readable
    age string: "5m", "2h", "3d".
    """
    if not timestamp:
        return "-"
    try:
        created_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        delta      = datetime.now(timezone.utc) - created_at
        seconds    = int(delta.total_seconds())

        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m"
        if seconds < 86400:
            return f"{seconds // 3600}h"
        return f"{seconds // 86400}d"
    except (ValueError, TypeError):
        return "-"

"""
cli/commands/switch_cmd.py

`guardops switch` — Phase 9: Blue-Green Traffic Switch.

Cuts live traffic from one deployment slot (blue or green) to the other
by patching a shared Kubernetes Service whose selector uses the
`guardops.io/slot` label.

HOW BLUE-GREEN WORKS IN GUARDOPS:

  Two Helm releases coexist in the same namespace (staging or prod):
    guardops-app-staging-blue   → pods labelled guardops.io/slot=blue
    guardops-app-staging-green  → pods labelled guardops.io/slot=green

  Each Helm release owns its own Deployment and a per-slot Service
  (used internally for readiness checks). Traffic is routed by a
  SHARED Service named `{project_name}` (e.g. "guardops-app") that
  uses `guardops.io/slot` as its pod selector.

  `guardops switch --slot green` patches that shared Service's selector
  so that all incoming traffic is forwarded to green pods. The switch is
  atomic (a single kubectl patch) and takes effect within one iptables
  propagation cycle (~1-2 seconds on most clusters).

SHARED SERVICE LIFECYCLE:
  The shared traffic Service does NOT belong to either Helm release — it
  is created by the first `guardops switch` call and is annotated with
  `guardops.io/managed-by=guardops` and `guardops.io/service-type=traffic`
  so it can be identified and cleaned up independently.

  If you delete it manually, the next `guardops switch` call recreates it.

SELECTOR USED:
  The shared Service selects pods by two labels:
    app.kubernetes.io/name: {project_name}   ← present on ALL slot pods
    guardops.io/slot: {target_slot}          ← only the active slot

  `app.kubernetes.io/name` is set by the Helm chart's `selectorLabels`
  helper to the chart name ("guardops-app"). It is the same for both
  blue and green releases because the chart name never changes, only the
  Helm release name changes. This is what allows a single Service selector
  to reach pods from either release.

USAGE:
  guardops switch --slot green                       # staging (default)
  guardops switch --slot blue  --env prod            # prod traffic switch
  guardops switch --slot green --namespace staging   # explicit namespace
  guardops switch --slot green --dry-run             # preview changes only

RELATED FILES:
  cli/commands/deploy_cmd.py              — deploys a slot (--slot blue/green)
  k8s/helm/guardops-app/values.yaml       — blueGreen section
  k8s/helm/guardops-app/templates/
    deployment.yaml                       — adds guardops.io/slot label to pods
  cli/utils/config.py                     — get_env_config, resolve_namespace
"""

import json
import subprocess
import sys
from typing import Optional

import click
from rich.table import Table
from rich import box

from cli.utils.config import (
    load_config,
    get_env_config,
    resolve_namespace,
)
from cli.utils.output import (
    console,
    info,
    success,
    warn,
    error,
)

# ── Constants ─────────────────────────────────────────────────────────────────

KUBECTL_TIMEOUT = 30

# Labels written onto the traffic Service so it can be identified independently
# of any Helm release.
TRAFFIC_SVC_LABELS: dict[str, str] = {
    "guardops.io/managed-by":   "guardops",
    "guardops.io/service-type": "traffic",
}

# Annotation key that records which slot is currently active.
ANN_ACTIVE_SLOT = "guardops.io/active-slot"

# Label key used by the Helm chart's deployment template to tag pods by slot.
SLOT_LABEL_KEY = "guardops.io/slot"

# Label key that is identical across all slot releases — lets the traffic
# Service target pods from either slot without knowing the release name.
APP_NAME_LABEL_KEY = "app.kubernetes.io/name"


# ── Click command ─────────────────────────────────────────────────────────────

@click.command("switch")
@click.option(
    "--slot",
    required=True,
    type=click.Choice(["blue", "green"], case_sensitive=False),
    help="Slot to activate. All traffic will be routed to this slot's pods.",
)
@click.option(
    "--env",
    default="staging",
    type=click.Choice(["local", "staging", "prod"], case_sensitive=False),
    show_default=True,
    help=(
        "Environment to switch traffic in. Determines the default namespace "
        "and service name when --namespace / --service-name are omitted."
    ),
)
@click.option(
    "--namespace", "-n",
    default=None,
    metavar="NS",
    help="Kubernetes namespace. Defaults to the namespace for --env.",
)
@click.option(
    "--service-name",
    default=None,
    metavar="NAME",
    help=(
        "Name of the shared traffic Service to patch. "
        "Defaults to the project name from .guardops.yaml (e.g. 'guardops-app')."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help=(
        "Preview what would change without applying anything. "
        "Shows the current selector and what it would become."
    ),
)
def switch_command(
    slot:         str,
    env:          str,
    namespace:    Optional[str],
    service_name: Optional[str],
    dry_run:      bool,
) -> None:
    """
    Switch live traffic to a blue or green deployment slot.

    Creates or patches a shared Kubernetes Service so that all traffic is
    forwarded to pods in the specified slot. The switch is instant and
    does not require a new image build or Helm upgrade.

    \b
    Prerequisites:
      Both slots must already be deployed:
        guardops deploy --env staging --slot blue
        guardops deploy --env staging --slot green

    \b
    Examples:
      guardops switch --slot green                  # activate green in staging
      guardops switch --slot blue   --env prod      # roll back to blue in prod
      guardops switch --slot green  --dry-run       # preview only
    """
    config = load_config()

    # ── Resolve namespace and service name ────────────────────────────────────
    resolved_ns = namespace or resolve_namespace(config, env)

    project_name  = config.get("project", {}).get("name", "guardops-app")
    resolved_svc  = service_name or project_name

    # ── Header ────────────────────────────────────────────────────────────────
    console.print()
    dry_label = " [dim](dry run)[/dim]" if dry_run else ""
    console.rule(
        f"[bold]GuardOps [cyan]Switch[/cyan][/bold] — "
        f"slot=[cyan]{slot}[/cyan] | "
        f"env=[cyan]{env}[/cyan] | "
        f"ns=[cyan]{resolved_ns}[/cyan]{dry_label}"
    )

    # ── Pre-flight: check both slots are deployed ─────────────────────────────
    info("Checking slot pod readiness...")
    blue_pods  = _get_slot_pods(resolved_ns, "blue",  project_name)
    green_pods = _get_slot_pods(resolved_ns, "green", project_name)

    _print_slot_summary(blue_pods, green_pods, slot)

    # Warn (don't block) if the target slot has no ready pods — the operator
    # may be intentionally switching to a slot during a deploy. They know best.
    target_pods = blue_pods if slot == "blue" else green_pods
    ready_count = sum(1 for p in target_pods if p.get("ready"))

    if not target_pods:
        warn(
            f"No pods found for the [cyan]{slot}[/cyan] slot in namespace "
            f"[cyan]{resolved_ns}[/cyan]. "
            f"Run [bold]guardops deploy --env {env} --slot {slot}[/bold] first."
        )
        if not dry_run:
            sys.exit(1)
    elif ready_count == 0:
        warn(
            f"[cyan]{slot}[/cyan] slot has {len(target_pods)} pod(s) but none are Ready. "
            "Traffic switch will proceed but requests may fail until pods are healthy."
        )

    # ── Check current Service state ───────────────────────────────────────────
    current_slot = _get_current_active_slot(resolved_svc, resolved_ns)

    if current_slot == slot and not dry_run:
        info(
            f"Traffic is already routed to [cyan]{slot}[/cyan]. No change needed."
        )
        return

    console.print()
    if current_slot:
        info(f"Current active slot : [dim]{current_slot}[/dim]")
    else:
        info("Shared traffic Service does not yet exist — will create it.")
    info(f"Target slot         : [bold cyan]{slot}[/bold cyan]")
    console.print()

    # ── Dry run — show what would change ──────────────────────────────────────
    if dry_run:
        _print_dry_run_diff(
            svc_name=resolved_svc,
            namespace=resolved_ns,
            project_name=project_name,
            current_slot=current_slot,
            target_slot=slot,
        )
        info("Dry run complete — no changes applied.")
        return

    # ── Apply: create or patch the shared traffic Service ────────────────────
    _apply_traffic_service(
        svc_name=resolved_svc,
        namespace=resolved_ns,
        project_name=project_name,
        slot=slot,
        exists=current_slot is not None,
    )

    # ── Verify: confirm Service selector was updated ──────────────────────────
    confirmed_slot = _get_current_active_slot(resolved_svc, resolved_ns)
    if confirmed_slot == slot:
        success(
            f"Traffic switched to [bold cyan]{slot}[/bold cyan] slot "
            f"(Service [cyan]{resolved_svc}[/cyan] in namespace [cyan]{resolved_ns}[/cyan])."
        )
    else:
        error(
            f"Service patch applied but active slot reads '{confirmed_slot}' — "
            "expected '{slot}'. Inspect the Service manually:\n"
            f"  kubectl get svc {resolved_svc} -n {resolved_ns} -o yaml"
        )
        sys.exit(1)

    # ── Post-switch advice ────────────────────────────────────────────────────
    inactive_slot = "green" if slot == "blue" else "blue"
    console.print()
    info(
        f"The [dim]{inactive_slot}[/dim] deployment is still running. "
        f"Once you're confident in [cyan]{slot}[/cyan], you can scale it down:\n"
        f"  kubectl scale deploy -l {SLOT_LABEL_KEY}={inactive_slot} "
        f"--replicas=0 -n {resolved_ns}"
    )
    info(
        f"To roll back: [bold green]guardops switch --slot {inactive_slot} "
        f"--env {env}[/bold green]"
    )
    console.print()


# ── Core logic ────────────────────────────────────────────────────────────────

def _apply_traffic_service(
    svc_name:    str,
    namespace:   str,
    project_name: str,
    slot:        str,
    exists:      bool,
) -> None:
    """
    Creates or patches the shared traffic Service with the new slot selector.

    Uses `kubectl apply -f -` (piping YAML via stdin) so the Service is
    created if absent or patched in-place if it already exists. The apply
    strategy is idempotent — safe to run multiple times.

    Args:
        svc_name:     Name of the shared traffic Service.
        namespace:    Kubernetes namespace.
        project_name: Chart app name — used as the base selector label value.
        slot:         "blue" or "green" — the slot to route traffic to.
        exists:       Whether the Service already exists (used only for logging).
    """
    action = "Patching" if exists else "Creating"
    info(f"{action} shared traffic Service [cyan]{svc_name}[/cyan]...")

    # Build the Service manifest. We use kubectl apply so this is fully
    # declarative — if the Service drifts from this spec, apply corrects it.
    svc_manifest = _build_service_manifest(svc_name, namespace, project_name, slot)

    ok, stdout, stderr = _kubectl_apply_stdin(svc_manifest)
    if not ok:
        error(f"kubectl apply failed: {stderr}")
        sys.exit(1)


def _build_service_manifest(
    svc_name:    str,
    namespace:   str,
    project_name: str,
    slot:        str,
) -> str:
    """
    Returns a YAML string for the shared traffic Service.

    The selector uses:
      app.kubernetes.io/name: {project_name}  — present on ALL slot pods
      guardops.io/slot: {slot}               — narrows to the active slot

    This combination avoids needing to know the Helm release names (which
    include the slot suffix) — any pod with the right app name + slot label
    will receive traffic.
    """
    return f"""apiVersion: v1
kind: Service
metadata:
  name: {svc_name}
  namespace: {namespace}
  labels:
    guardops.io/managed-by: guardops
    guardops.io/service-type: traffic
  annotations:
    guardops.io/active-slot: {slot}
    guardops.io/switch-tool: "guardops switch"
spec:
  type: ClusterIP
  selector:
    {APP_NAME_LABEL_KEY}: {project_name}
    {SLOT_LABEL_KEY}: {slot}
  ports:
    - name: http
      protocol: TCP
      port: 80
      targetPort: 8080
"""


# ── Slot pod queries ──────────────────────────────────────────────────────────

def _get_slot_pods(namespace: str, slot: str, project_name: str) -> list[dict]:
    """
    Returns a summary list of pods belonging to a given slot.

    Filters by both the app name label (to scope to this project) and the
    slot label (to scope to this slot). Each result dict has:
      name, namespace, ready, phase, slot
    """
    label_selector = f"{APP_NAME_LABEL_KEY}={project_name},{SLOT_LABEL_KEY}={slot}"
    ok, stdout, stderr = _run_kubectl([
        "get", "pods",
        "-l", label_selector,
        "-n", namespace,
        "-o", "json",
    ])

    if not ok or not stdout:
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []

    results = []
    for item in data.get("items", []):
        meta      = item.get("metadata", {})
        status    = item.get("status", {})
        phase     = status.get("phase", "Unknown")

        # A pod is Ready when all containers report Ready in the conditions list.
        conditions = status.get("conditions", [])
        ready = any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in conditions
        )

        results.append({
            "name":      meta.get("name", "unknown"),
            "namespace": meta.get("namespace", namespace),
            "phase":     phase,
            "ready":     ready,
            "slot":      slot,
        })

    return results


def _get_current_active_slot(svc_name: str, namespace: str) -> Optional[str]:
    """
    Returns the currently active slot from the traffic Service annotation,
    or None if the Service does not exist.
    """
    ok, stdout, _ = _run_kubectl([
        "get", "svc", svc_name,
        "-n", namespace,
        "-o", "json",
    ])

    if not ok or not stdout:
        return None

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None

    # Read the active-slot annotation written by this command.
    annotations = data.get("metadata", {}).get("annotations", {})
    return annotations.get(ANN_ACTIVE_SLOT)


# ── Display helpers ───────────────────────────────────────────────────────────

def _print_slot_summary(
    blue_pods:  list[dict],
    green_pods: list[dict],
    target:     str,
) -> None:
    """Renders a compact two-column summary of both slots' pod readiness."""
    table = Table(
        box=box.ROUNDED,
        border_style="dim",
        show_header=True,
        header_style="bold",
        padding=(0, 1),
    )

    table.add_column("Slot",  min_width=8)
    table.add_column("Pods",  min_width=6,  justify="center")
    table.add_column("Ready", min_width=6,  justify="center")
    table.add_column("Status", min_width=12)

    for slot_name, pods in [("blue", blue_pods), ("green", green_pods)]:
        is_target    = slot_name == target
        slot_style   = "bold cyan" if is_target else "dim"
        total        = len(pods)
        ready        = sum(1 for p in pods if p.get("ready"))

        if total == 0:
            status_str   = "[yellow]not deployed[/yellow]"
            ready_str    = "-"
        elif ready == total:
            status_str   = "[green]healthy[/green]"
            ready_str    = f"[green]{ready}/{total}[/green]"
        else:
            status_str   = "[yellow]degraded[/yellow]"
            ready_str    = f"[yellow]{ready}/{total}[/yellow]"

        prefix = "-> " if is_target else "   "
        table.add_row(
            f"[{slot_style}]{prefix}{slot_name}[/{slot_style}]",
            str(total),
            ready_str,
            status_str,
        )

    console.print(table)
    console.print()


def _print_dry_run_diff(
    svc_name:     str,
    namespace:    str,
    project_name: str,
    current_slot: Optional[str],
    target_slot:  str,
) -> None:
    """Shows what the switch would do without applying anything."""
    console.print()
    console.print("  [bold]Dry run — proposed changes:[/bold]")
    console.print()

    if current_slot is None:
        console.print(
            f"  [dim]CREATE[/dim] Service [cyan]{svc_name}[/cyan] "
            f"in namespace [cyan]{namespace}[/cyan]"
        )
    else:
        console.print(
            f"  [dim]PATCH[/dim]  Service [cyan]{svc_name}[/cyan] "
            f"in namespace [cyan]{namespace}[/cyan]"
        )

    console.print()
    console.print(f"  Selector before: {APP_NAME_LABEL_KEY}={project_name}")
    if current_slot:
        console.print(f"                   {SLOT_LABEL_KEY}=[dim]{current_slot}[/dim]")

    console.print(f"  Selector after:  {APP_NAME_LABEL_KEY}={project_name}")
    console.print(f"                   {SLOT_LABEL_KEY}=[bold cyan]{target_slot}[/bold cyan]")
    console.print()

    manifest = _build_service_manifest(svc_name, namespace, project_name, target_slot)
    console.print("  [dim]Full manifest that would be applied:[/dim]")
    for line in manifest.splitlines():
        console.print(f"  [dim]{line}[/dim]")
    console.print()


# ── subprocess helpers ────────────────────────────────────────────────────────

def _run_kubectl(args: list[str]) -> tuple[bool, str, str]:
    """
    Runs `kubectl {args}` and returns (success, stdout, stderr).
    Never raises — always returns a structured tuple so the caller decides
    how to handle failures.
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
        return False, "", f"kubectl {args[0] if args else '?'} timed out after {KUBECTL_TIMEOUT}s"
    except FileNotFoundError:
        return False, "", "kubectl not found — is it installed and on PATH?"
    except Exception as exc:
        return False, "", f"Unexpected error running kubectl: {exc}"


def _kubectl_apply_stdin(manifest: str) -> tuple[bool, str, str]:
    """
    Runs `kubectl apply -f -` with the given YAML piped to stdin.

    This is the idempotent apply strategy — creates the resource if it
    doesn't exist, patches it if it does, and is a no-op if nothing changed.

    Args:
        manifest: YAML string to apply.

    Returns:
        (success, stdout, stderr)
    """
    cmd = ["kubectl", "apply", "-f", "-"]
    try:
        result = subprocess.run(
            cmd,
            input=manifest,
            capture_output=True,
            text=True,
            timeout=KUBECTL_TIMEOUT,
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", f"kubectl apply timed out after {KUBECTL_TIMEOUT}s"
    except FileNotFoundError:
        return False, "", "kubectl not found — is it installed and on PATH?"
    except Exception as exc:
        return False, "", f"Unexpected error running kubectl apply: {exc}"

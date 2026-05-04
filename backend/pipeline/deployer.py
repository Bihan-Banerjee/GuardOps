"""
backend/pipeline/deployer.py

Handles deploying to Kubernetes.

Phase 1/2: deploy_local() — raw kubectl apply, k3d, manual rollback
Phase 3+:  deploy_helm()  — helm upgrade --install --atomic, works on both
                            k3d (local) and EKS (prod)

WHY HELM:
  - `--atomic` gives us automatic rollback for free. If a rollout fails,
    Helm reverts to the previous release. No manual cleanup needed.
  - `helm rollback <release> <revision>` lets operators revert to any
    previous known-good state with one command.
  - `values.yaml` + `values-prod.yaml` cleanly separates local and prod
    config without duplicating manifests.
  - Helm tracks release history, so `helm history` shows every deploy.

Phase 1/2 functions are kept for reference and tests but deploy_cmd.py
now calls deploy_helm() for both --env local and --env prod.
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cli.utils.system import run_command
from cli.utils.output import info, warn, error, console


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DeployResult:
    """Result of any deployment operation (kubectl or Helm)."""
    success: bool
    namespace: str
    deployment_name: str
    replicas: int = 1
    service_url: str = ""
    error_message: str = ""
    rollback_applied: bool = False
    helm_release: str = ""
    helm_revision: int = 0


@dataclass
class RollbackResult:
    """Result of a helm rollback operation."""
    success: bool
    release: str
    rolled_back_to: int = 0
    error_message: str = ""


# ---------------------------------------------------------------------------
# Phase 3: Helm deploy (primary path)
# ---------------------------------------------------------------------------

def deploy_helm(
    project_name: str,
    image_ref: str,
    namespace: str = "default",
    replicas: int = 1,
    env: str = "local",
    chart_path: Optional[str] = None,
    extra_values: Optional[dict] = None,
) -> DeployResult:
    """
    Deploys using Helm — works on both k3d (local) and EKS (prod).

    Uses `helm upgrade --install --atomic` which means:
    - If the release doesn't exist: install it
    - If it does exist: upgrade it
    - --atomic: if the rollout fails or times out, automatically revert
      to the previous release. No manual cleanup needed.

    Args:
        project_name: Helm release name and app name
        image_ref:    Full image reference e.g. "my-api:abc123" or
                      "123.dkr.ecr.us-east-1.amazonaws.com/my-api:abc123"
        namespace:    Kubernetes namespace
        replicas:     Number of pod replicas
        env:          "local" or "prod" — controls which values file is used
        chart_path:   Path to Helm chart directory (auto-detected if None)
        extra_values: Additional --set overrides as a dict

    Returns:
        DeployResult with success/failure, helm release name, and revision.
    """
    if not _helm_available():
        return DeployResult(
            success=False,
            namespace=namespace,
            deployment_name=project_name,
            error_message="helm not found. Install from https://helm.sh/docs/intro/install/",
        )

    # Auto-detect chart path relative to project root
    if chart_path is None:
        chart_path = _find_chart_path()
    if not chart_path:
        return DeployResult(
            success=False,
            namespace=namespace,
            deployment_name=project_name,
            error_message=(
                "Helm chart not found. Expected at k8s/helm/guardops-app/. "
                "Run from the GuardOps project root."
            ),
        )

    # Parse image ref into repository and tag
    image_repo, image_tag = _split_image_ref(image_ref)
    pull_policy = "Never" if env == "local" else "Always"

    release_name = _sanitize_release_name(project_name)

    # Build the helm command
    cmd = [
        "helm", "upgrade", "--install",
        release_name,           # release name
        chart_path,             # chart directory
        "--namespace", namespace,
        "--create-namespace",   # create namespace if it doesn't exist
        "--atomic",             # auto-rollback on failure
        "--timeout", "3m",      # wait up to 3 minutes for rollout
        "--set", f"app.name={project_name}",
        "--set", f"image.repository={image_repo}",
        "--set", f"image.tag={image_tag}",
        "--set", f"image.pullPolicy={pull_policy}",
        "--set", f"replicaCount={replicas}",
    ]

    # Apply prod values on top of base values
    if env == "prod":
        prod_values = Path(chart_path) / "values-prod.yaml"
        if prod_values.exists():
            cmd += ["-f", str(prod_values)]

    # Apply any extra --set overrides
    if extra_values:
        for key, value in extra_values.items():
            cmd += ["--set", f"{key}={value}"]

    info(f"Running helm upgrade --install for release [cyan]{release_name}[/cyan]...")

    result = run_command(cmd, capture_output=False, show_command=True)

    if result.returncode != 0:
        return DeployResult(
            success=False,
            namespace=namespace,
            deployment_name=project_name,
            helm_release=release_name,
            error_message=(
                f"helm upgrade --install failed (exit {result.returncode}). "
                "Helm automatically rolled back to the previous release if one existed."
            ),
        )

    # Get the revision number from helm history
    revision = _get_helm_revision(release_name, namespace)
    service_url = _get_ingress_url(release_name, namespace)

    return DeployResult(
        success=True,
        namespace=namespace,
        deployment_name=project_name,
        replicas=replicas,
        service_url=service_url,
        helm_release=release_name,
        helm_revision=revision,
    )


def rollback_helm(
    release_name: str,
    namespace: str = "default",
    revision: int = 0,
) -> RollbackResult:
    """
    Rolls back a Helm release to a previous revision.

    `helm rollback <release> 0` rolls back to the previous release
    (revision - 1). Pass a specific revision number to roll back further.

    Args:
        release_name: Helm release name (usually the project name)
        namespace:    Kubernetes namespace
        revision:     Target revision (0 = previous release)

    Returns:
        RollbackResult with success/failure and the revision rolled back to.
    """
    if not _helm_available():
        return RollbackResult(
            success=False,
            release=release_name,
            error_message="helm not found.",
        )

    cmd = [
        "helm", "rollback",
        release_name,
        str(revision),
        "--namespace", namespace,
        "--wait",       # wait for rollback to complete
        "--timeout", "2m",
    ]

    info(f"Rolling back release [cyan]{release_name}[/cyan]"
         f" to revision [cyan]{revision or 'previous'}[/cyan]...")

    result = run_command(cmd, capture_output=True, show_command=True)

    if result.returncode != 0:
        return RollbackResult(
            success=False,
            release=release_name,
            error_message=f"helm rollback failed: {result.stderr.strip()}",
        )

    current_revision = _get_helm_revision(release_name, namespace)
    return RollbackResult(
        success=True,
        release=release_name,
        rolled_back_to=current_revision,
    )


def get_helm_history(
    release_name: str,
    namespace: str = "default",
) -> list[dict]:
    """
    Returns the deployment history for a Helm release.

    Each entry has: revision, updated, status, chart, app_version, description.
    Returns empty list if release doesn't exist or helm is not available.
    """
    result = run_command(
        ["helm", "history", release_name,
         "--namespace", namespace,
         "--output", "json"],
        capture_output=True,
    )
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return []


# ---------------------------------------------------------------------------
# Phase 1/2: kubectl deploy (kept for reference and backward compat)
# ---------------------------------------------------------------------------

def deploy_local(
    project_name: str,
    image_ref: str,
    namespace: str = "default",
    replicas: int = 1,
    port: int = 8080,
) -> DeployResult:
    """
    Phase 1/2 deploy using raw kubectl apply.
    Kept for backward compatibility. New code should use deploy_helm().

    The Phase 3 upgrade path:
      deploy_local() -> deploy_helm(env="local")
    Which gives you automatic rollback, history, and Ingress for free.
    """
    info(f"Deploying [cyan]{image_ref}[/cyan] to namespace [cyan]{namespace}[/cyan]")

    if not import_image_to_k3d(image_ref):
        return DeployResult(
            success=False,
            namespace=namespace,
            deployment_name=project_name,
            error_message="Failed to import image into k3d cluster.",
        )

    _ensure_namespace(namespace)

    deployment_yaml = _generate_deployment_manifest(
        project_name, image_ref, namespace, replicas, port
    )
    if not _kubectl_apply(deployment_yaml):
        return DeployResult(
            success=False,
            namespace=namespace,
            deployment_name=project_name,
            error_message="Failed to apply Deployment manifest.",
        )

    service_yaml = _generate_service_manifest(project_name, namespace, port)
    if not _kubectl_apply(service_yaml):
        rolled_back = rollback_partial_deploy(project_name, namespace)
        return DeployResult(
            success=False,
            namespace=namespace,
            deployment_name=project_name,
            error_message="Failed to apply Service manifest.",
            rollback_applied=rolled_back,
        )

    info("Waiting for pods to become ready...")
    if not _wait_for_rollout(project_name, namespace, timeout_seconds=120):
        rolled_back = rollback_partial_deploy(project_name, namespace)
        return DeployResult(
            success=False,
            namespace=namespace,
            deployment_name=project_name,
            error_message=(
                "Deployment timed out waiting for pods to be ready. "
                "Check pod logs with: guardops logs"
            ),
            rollback_applied=rolled_back,
        )

    service_url = _get_service_url(project_name, namespace, port)
    return DeployResult(
        success=True,
        namespace=namespace,
        deployment_name=project_name,
        replicas=replicas,
        service_url=service_url,
    )


def rollback_partial_deploy(
    project_name: str,
    namespace: str = "default",
) -> bool:
    """Phase 1/2 manual cleanup. Replaced by helm --atomic in Phase 3."""
    warn(f"Cleaning up partial resources for [cyan]{project_name}[/cyan]...")
    d = run_command(
        ["kubectl", "delete", "deployment", project_name,
         "-n", namespace, "--ignore-not-found"],
        capture_output=True,
    )
    s = run_command(
        ["kubectl", "delete", "service", project_name,
         "-n", namespace, "--ignore-not-found"],
        capture_output=True,
    )
    ok = d.returncode == 0 and s.returncode == 0
    if ok:
        info("Partial resources removed.")
    else:
        warn("Cleanup may be incomplete. Run `kubectl get all` to check.")
    return ok


# ---------------------------------------------------------------------------
# Kubernetes status queries (used by both phases)
# ---------------------------------------------------------------------------

def get_deployment_status(
    deployment_name: str,
    namespace: str = "default"
) -> dict:
    """Returns deployment status dict, or empty dict on any error."""
    result = run_command(
        ["kubectl", "get", "deployment", deployment_name,
         "-n", namespace, "-o", "json"],
        capture_output=True
    )
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
        status = data.get("status", {})
        spec = data.get("spec", {})
        return {
            "name": deployment_name,
            "namespace": namespace,
            "desired_replicas": spec.get("replicas", 0),
            "ready_replicas": status.get("readyReplicas", 0),
            "available_replicas": status.get("availableReplicas", 0),
            "updated_replicas": status.get("updatedReplicas", 0),
            "conditions": [
                {
                    "type": c.get("type"),
                    "status": c.get("status"),
                    "message": c.get("message", ""),
                }
                for c in status.get("conditions", [])
            ]
        }
    except (json.JSONDecodeError, KeyError):
        return {}


def get_pods(deployment_name: str, namespace: str = "default") -> list[dict]:
    """Returns list of pod dicts for a deployment, or empty list on error."""
    result = run_command(
        ["kubectl", "get", "pods",
         "-l", f"app.kubernetes.io/instance={deployment_name}",
         "-n", namespace, "-o", "json"],
        capture_output=True
    )
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
        pods = []
        for item in data.get("items", []):
            metadata = item.get("metadata", {})
            status = item.get("status", {})
            pods.append({
                "name": metadata.get("name", ""),
                "phase": status.get("phase", "Unknown"),
                "ready": _is_pod_ready(item),
                "restarts": _get_restart_count(item),
                "node": spec_node(item),
                "age": metadata.get("creationTimestamp", ""),
            })
        return pods
    except (json.JSONDecodeError, KeyError):
        return []


# ---------------------------------------------------------------------------
# Private Helm helpers
# ---------------------------------------------------------------------------

def _helm_available() -> bool:
    """Checks if helm binary is on PATH."""
    import shutil
    return shutil.which("helm") is not None


def _find_chart_path() -> Optional[str]:
    """
    Searches for the Helm chart directory from the current working directory
    upward. Returns the path if found, None if not.
    """
    candidates = [
        "k8s/helm/guardops-app",
        "../k8s/helm/guardops-app",
        "../../k8s/helm/guardops-app",
    ]
    for candidate in candidates:
        p = Path(candidate)
        if p.exists() and (p / "Chart.yaml").exists():
            return str(p)
    return None


def _split_image_ref(image_ref: str) -> tuple[str, str]:
    """
    Splits 'repo:tag' into ('repo', 'tag').
    Handles ECR refs like '123.dkr.ecr.us-east-1.amazonaws.com/app:abc123'.
    If no tag present, defaults to 'latest'.
    """
    if ":" in image_ref:
        # Split on the LAST colon to handle ECR registry URLs (which contain colons)
        idx = image_ref.rfind(":")
        return image_ref[:idx], image_ref[idx + 1:]
    return image_ref, "latest"


def _sanitize_release_name(name: str) -> str:
    """
    Helm release names must be lowercase alphanumeric + hyphens, max 53 chars.
    """
    import re
    sanitized = name.lower()
    sanitized = re.sub(r"[^a-z0-9-]", "-", sanitized)
    sanitized = sanitized.strip("-")[:53]
    return sanitized or "guardops-app"


def _get_helm_revision(release_name: str, namespace: str) -> int:
    """Gets the current revision number of a Helm release."""
    result = run_command(
        ["helm", "status", release_name,
         "--namespace", namespace,
         "--output", "json"],
        capture_output=True,
    )
    if result.returncode != 0:
        return 0
    try:
        data = json.loads(result.stdout)
        return data.get("version", 0)
    except (json.JSONDecodeError, KeyError):
        return 0


def _get_ingress_url(release_name: str, namespace: str) -> str:
    """
    Gets the URL from the Ingress resource created by Helm.
    Falls back to a kubectl port-forward suggestion if not found.
    """
    result = run_command(
        ["kubectl", "get", "ingress",
         "-l", f"app.kubernetes.io/instance={release_name}",
         "-n", namespace,
         "-o", "jsonpath={.items[0].spec.rules[0].host}"],
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        host = result.stdout.strip()
        return f"http://{host}"
    return "http://localhost (run: kubectl port-forward svc/<name> 8080:80)"


# ---------------------------------------------------------------------------
# Private kubectl helpers (Phase 1/2)
# ---------------------------------------------------------------------------

def _ensure_namespace(namespace: str) -> None:
    if namespace == "default":
        return
    result = run_command(
        ["kubectl", "create", "namespace", namespace],
        capture_output=True
    )
    if result.returncode != 0 and "already exists" not in result.stderr:
        warn(f"Could not create namespace '{namespace}': {result.stderr.strip()}")


def _kubectl_apply(manifest_yaml: str) -> bool:
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=manifest_yaml,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    if result.returncode == 0:
        console.print(f"[dim green]  {result.stdout.strip()}[/dim green]")
        return True
    error(f"kubectl apply failed: {result.stderr.strip()}")
    return False


def _generate_deployment_manifest(
    name: str, image: str, namespace: str, replicas: int, port: int
) -> str:
    return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  namespace: {namespace}
  labels:
    app: {name}
    managed-by: guardops
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app: {name}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: {name}
    spec:
      containers:
        - name: {name}
          image: {image}
          imagePullPolicy: Never
          ports:
            - containerPort: {port}
              name: http
          resources:
            requests:
              memory: "64Mi"
              cpu: "50m"
            limits:
              memory: "256Mi"
              cpu: "200m"
          livenessProbe:
            httpGet:
              path: /healthz
              port: {port}
            initialDelaySeconds: 10
            periodSeconds: 15
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /ready
              port: {port}
            initialDelaySeconds: 5
            periodSeconds: 10
            failureThreshold: 3
"""


def _generate_service_manifest(name: str, namespace: str, port: int) -> str:
    return f"""apiVersion: v1
kind: Service
metadata:
  name: {name}
  namespace: {namespace}
  labels:
    app: {name}
    managed-by: guardops
spec:
  selector:
    app: {name}
  ports:
    - protocol: TCP
      port: {port}
      targetPort: {port}
      name: http
  type: NodePort
"""


def _wait_for_rollout(
    deployment_name: str, namespace: str, timeout_seconds: int = 120
) -> bool:
    result = run_command(
        ["kubectl", "rollout", "status",
         f"deployment/{deployment_name}",
         "-n", namespace,
         f"--timeout={timeout_seconds}s"],
        capture_output=False
    )
    return result.returncode == 0


def _get_service_url(name: str, namespace: str, port: int) -> str:
    result = run_command(
        ["kubectl", "get", "service", name, "-n", namespace,
         "-o", "jsonpath={.spec.ports[0].nodePort}"],
        capture_output=True
    )
    if result.returncode == 0 and result.stdout.strip():
        return f"http://localhost:{result.stdout.strip()}"
    return f"http://localhost:{port} (kubectl port-forward may be needed)"


def _is_pod_ready(pod: dict) -> bool:
    for c in pod.get("status", {}).get("conditions", []):
        if c.get("type") == "Ready":
            return c.get("status") == "True"
    return False


def _get_restart_count(pod: dict) -> int:
    containers = pod.get("status", {}).get("containerStatuses", [])
    return sum(c.get("restartCount", 0) for c in containers)


def spec_node(pod: dict) -> str:
    return pod.get("spec", {}).get("nodeName", "unknown")


def import_image_to_k3d(image_ref: str, cluster_name: str = "guardops-local") -> bool:
    """
    Imports a local Docker image into k3d's internal registry.
    Required for local Helm deploys (imagePullPolicy: Never).
    Not needed in prod where images are pulled from ECR.
    """
    info(f"Importing [cyan]{image_ref}[/cyan] into k3d cluster [cyan]{cluster_name}[/cyan]...")
    result = run_command(
        ["k3d", "image", "import", image_ref, "-c", cluster_name],
        capture_output=False
    )
    return result.returncode == 0
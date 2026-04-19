"""
backend/pipeline/deployer.py

Handles deploying to Kubernetes (local cluster in Phase 1).

Phase 1 strategy: Use raw kubectl commands to apply manifests.
Phase 3 upgrade: Replace with Helm charts for full production deploys.

HOW LOCAL K8S WORKS:
  k3d creates a lightweight Kubernetes cluster using Docker containers.
  kubectl talks to it via ~/.kube/config (auto-configured by k3d).
  We generate Kubernetes YAML manifests as strings and apply them
  via `kubectl apply -f -` (the `-` means "read from stdin").
"""

import json
import time
from dataclasses import dataclass
from typing import Optional

from cli.utils.system import run_command, get_command_output
from cli.utils.output import info, warn, error, console


@dataclass
class DeployResult:
    """Result of a Kubernetes deployment operation."""
    success: bool
    namespace: str
    deployment_name: str
    replicas: int = 1
    service_url: str = ""
    error_message: str = ""


def deploy_local(
    project_name: str,
    image_ref: str,
    namespace: str = "default",
    replicas: int = 1,
    port: int = 8080,
) -> DeployResult:
    """
    Deploys a Docker image to a local Kubernetes cluster using kubectl.

    Phase 1 approach: Generate Kubernetes manifests as YAML strings
    and pipe them to `kubectl apply`. No Helm required yet.

    Args:
        project_name: Used as the Deployment and Service name
        image_ref:    Full image reference, e.g. "my-api:latest"
        namespace:    Kubernetes namespace to deploy into
        replicas:     Number of pod replicas to run
        port:         Port the container listens on

    Returns:
        DeployResult with success/failure and service URL.
    """
    info(f"Deploying [cyan]{image_ref}[/cyan] to namespace [cyan]{namespace}[/cyan]")

    if not import_image_to_k3d(image_ref):
        return DeployResult(
            success=False,
            namespace=namespace,
            deployment_name=project_name,
            error_message="Failed to import image into k3d cluster"
        )

    # ── Step 1: Ensure namespace exists ──────────────────────────────────────
    _ensure_namespace(namespace)

    # ── Step 2: Generate and apply the Deployment manifest ───────────────────
    deployment_yaml = _generate_deployment_manifest(
        project_name, image_ref, namespace, replicas, port
    )

    apply_result = _kubectl_apply(deployment_yaml)
    if not apply_result:
        return DeployResult(
            success=False,
            namespace=namespace,
            deployment_name=project_name,
            error_message="Failed to apply Deployment manifest"
        )

    # ── Step 3: Generate and apply the Service manifest ──────────────────────
    service_yaml = _generate_service_manifest(project_name, namespace, port)
    apply_result = _kubectl_apply(service_yaml)
    if not apply_result:
        return DeployResult(
            success=False,
            namespace=namespace,
            deployment_name=project_name,
            error_message="Failed to apply Service manifest"
        )

    # ── Step 4: Wait for pods to be ready ────────────────────────────────────
    info("Waiting for pods to become ready...")
    ready = _wait_for_rollout(project_name, namespace, timeout_seconds=120)

    if not ready:
        return DeployResult(
            success=False,
            namespace=namespace,
            deployment_name=project_name,
            error_message="Deployment timed out waiting for pods to be ready"
        )

    # ── Step 5: Get service URL ───────────────────────────────────────────────
    service_url = _get_service_url(project_name, namespace, port)

    return DeployResult(
        success=True,
        namespace=namespace,
        deployment_name=project_name,
        replicas=replicas,
        service_url=service_url,
    )


def get_deployment_status(
    deployment_name: str,
    namespace: str = "default"
) -> dict:
    """
    Retrieves the current status of a Kubernetes deployment.

    Uses `kubectl get deployment -o json` to get machine-readable output,
    then parses the JSON to extract the fields we care about.

    Returns a dict with status info, or empty dict if deployment not found.
    """
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
            # Conditions list contains the human-readable status messages
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
    """
    Gets all pods for a deployment.

    Kubernetes labels pods with app=<deployment_name>.
    We use -l (label selector) to filter pods by this label.

    Returns list of dicts with pod info.
    """
    result = run_command(
        ["kubectl", "get", "pods",
         "-l", f"app={deployment_name}",
         "-n", namespace,
         "-o", "json"],
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


# ─── Private helper functions ──────────────────────────────────────────────────

def _ensure_namespace(namespace: str) -> None:
    """
    Creates a Kubernetes namespace if it doesn't exist.
    Uses --dry-run=client approach to be idempotent (safe to run multiple times).
    """
    if namespace == "default":
        # 'default' namespace always exists in Kubernetes — skip creation
        return

    result = run_command(
        ["kubectl", "create", "namespace", namespace],
        capture_output=True
    )
    # returncode != 0 is fine if namespace already exists
    if result.returncode != 0 and "already exists" not in result.stderr:
        warn(f"Could not create namespace '{namespace}': {result.stderr.strip()}")


def _generate_deployment_manifest(
    name: str,
    image: str,
    namespace: str,
    replicas: int,
    port: int
) -> str:
    """
    Generates a Kubernetes Deployment manifest as a YAML string.

    A Deployment tells Kubernetes:
    - HOW MANY pods to run (replicas)
    - WHAT container to run (image)
    - HOW to update pods (strategy)
    - WHEN a pod is healthy (livenessProbe, readinessProbe)

    The | character in f-strings with triple-quotes is just cosmetic
    — the YAML is the actual content.
    """
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
          # imagePullPolicy: Never tells k8s to NOT try to pull from a registry.
          # For local development with locally-built images, this is essential.
          # In Phase 3 (ECR), this changes to Always.
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
"""


def _generate_service_manifest(name: str, namespace: str, port: int) -> str:
    """
    Generates a Kubernetes Service manifest.

    A Service creates a stable network endpoint to reach your pods.
    Without a Service, pods are only reachable by their internal IP,
    which changes every time a pod restarts.

    NodePort type exposes the service on a port of the host machine
    (the k3d container acting as a node), making it accessible locally.
    """
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


def _kubectl_apply(manifest_yaml: str) -> bool:
    """
    Applies a Kubernetes manifest by piping it to kubectl apply -f -.

    We use subprocess.run directly here because run_command wraps it
    and we need to pass stdin (the YAML string).

    Returns True on success, False on failure.
    """
    import subprocess

    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"],  # - means "read from stdin"
        input=manifest_yaml,              # Pass the YAML string as stdin
        text=True,                        # Treat input/output as strings
        capture_output=True
    )

    if result.returncode == 0:
        console.print(f"[dim green]  {result.stdout.strip()}[/dim green]")
        return True
    else:
        error(f"kubectl apply failed: {result.stderr.strip()}")
        return False


def _wait_for_rollout(
    deployment_name: str,
    namespace: str,
    timeout_seconds: int = 120
) -> bool:
    """
    Waits for a deployment rollout to complete.

    `kubectl rollout status` watches the deployment and exits when:
    - All pods are running and ready → returns 0 (success)
    - Timeout is reached → returns non-zero (failure)

    The --timeout flag uses Go duration format: "120s", "5m", "1h".
    """
    result = run_command(
        ["kubectl", "rollout", "status",
         f"deployment/{deployment_name}",
         "-n", namespace,
         f"--timeout={timeout_seconds}s"],
        capture_output=False  # Show rollout progress live
    )
    return result.returncode == 0


def _get_service_url(name: str, namespace: str, port: int) -> str:
    """
    Gets the URL to access the service locally.

    For k3d clusters, the service is accessible at localhost:<nodePort>.
    We query kubectl to find the actual NodePort assigned.
    """
    result = run_command(
        ["kubectl", "get", "service", name,
         "-n", namespace,
         "-o", "jsonpath={.spec.ports[0].nodePort}"],
        capture_output=True
    )
    if result.returncode == 0 and result.stdout.strip():
        node_port = result.stdout.strip()
        return f"http://localhost:{node_port}"
    return f"http://localhost:{port} (kubectl port-forward may be needed)"


def _is_pod_ready(pod: dict) -> bool:
    """Checks if a pod's Ready condition is True."""
    conditions = pod.get("status", {}).get("conditions", [])
    for condition in conditions:
        if condition.get("type") == "Ready":
            return condition.get("status") == "True"
    return False


def _get_restart_count(pod: dict) -> int:
    """Returns total restart count across all containers in a pod."""
    containers = pod.get("status", {}).get("containerStatuses", [])
    return sum(c.get("restartCount", 0) for c in containers)


def spec_node(pod: dict) -> str:
    """Returns the node a pod is scheduled on."""
    return pod.get("spec", {}).get("nodeName", "unknown")

def import_image_to_k3d(image_ref: str, cluster_name: str = "guardops-local") -> bool:
    """
    Imports a local Docker image into k3d's internal registry.
    Required because k3d runs isolated from Docker Desktop's image cache.
    Without this, pods fail with ErrImageNeverPull or ImagePullBackOff.
    """
    from cli.utils.system import run_command
    from cli.utils.output import info

    info(f"Importing [cyan]{image_ref}[/cyan] into k3d cluster [cyan]{cluster_name}[/cyan]...")
    result = run_command(
        ["k3d", "image", "import", image_ref, "-c", cluster_name],
        capture_output=False
    )
    return result.returncode == 0
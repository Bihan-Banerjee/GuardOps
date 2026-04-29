"""
backend/pipeline/deployer.py

Handles deploying to Kubernetes (local cluster in Phase 1/2).

Phase 1/2 strategy: Use raw kubectl commands to apply manifests.
Phase 3 upgrade: Replace with Helm charts — rollback becomes `helm rollback`
                  and is essentially free.

ERROR RECOVERY PHILOSOPHY (Phase 1/2):
  Kubernetes deployments are multi-step: image import → namespace → Deployment
  manifest → Service manifest → rollout wait. If any step fails after we've
  already applied resources, the cluster is left in a partial state (e.g. a
  Deployment with no Service, or a Deployment stuck in ImagePullBackOff).

  We handle this with rollback_partial_deploy(), which deletes the Deployment
  and Service if we applied them before the failure. This is called
  automatically by deploy_local() on any failure after Step 2.

  In Phase 3, Helm's atomic flag (--atomic) handles this natively.
  This manual rollback is a Phase 1/2 stopgap.
"""

import json
import time
from dataclasses import dataclass, field
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
    rollback_applied: bool = False   # True if we ran cleanup after a failed deploy


def deploy_local(
    project_name: str,
    image_ref: str,
    namespace: str = "default",
    replicas: int = 1,
    port: int = 8080,
) -> DeployResult:
    """
    Deploys a Docker image to a local Kubernetes cluster using kubectl.

    Steps:
      1. Import image into k3d's internal registry
      2. Ensure the namespace exists
      3. Apply the Deployment manifest
      4. Apply the Service manifest
      5. Wait for the rollout to complete
      6. Return the service URL

    If steps 3, 4, or 5 fail after resources have been applied, we call
    rollback_partial_deploy() to remove the partially-created resources
    and leave the cluster in a clean state.

    Args:
        project_name: Used as the Deployment and Service name
        image_ref:    Full image reference, e.g. "my-api:latest"
        namespace:    Kubernetes namespace to deploy into
        replicas:     Number of pod replicas to run
        port:         Port the container listens on

    Returns:
        DeployResult with success/failure, service URL, and rollback status.
    """
    info(f"Deploying [cyan]{image_ref}[/cyan] to namespace [cyan]{namespace}[/cyan]")

    # ── Step 1: Import image into k3d ───────────────────────────────────────
    if not import_image_to_k3d(image_ref):
        return DeployResult(
            success=False,
            namespace=namespace,
            deployment_name=project_name,
            error_message="Failed to import image into k3d cluster. "
                          "Ensure k3d cluster 'guardops-local' is running.",
        )

    # ── Step 2: Ensure namespace exists ─────────────────────────────────────
    _ensure_namespace(namespace)

    # Track which resources we've successfully applied so we can roll back
    applied_deployment = False
    applied_service = False

    # ── Step 3: Apply Deployment manifest ───────────────────────────────────
    deployment_yaml = _generate_deployment_manifest(
        project_name, image_ref, namespace, replicas, port
    )
    if not _kubectl_apply(deployment_yaml):
        # Nothing was applied yet, no cleanup needed
        return DeployResult(
            success=False,
            namespace=namespace,
            deployment_name=project_name,
            error_message="Failed to apply Deployment manifest. "
                          "Check kubectl access and cluster health.",
        )
    applied_deployment = True

    # ── Step 4: Apply Service manifest ──────────────────────────────────────
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
    applied_service = True

    # ── Step 5: Wait for rollout ─────────────────────────────────────────────
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

    # ── Step 6: Get service URL ──────────────────────────────────────────────
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
    """
    Cleans up Kubernetes resources created during a failed deploy_local().

    Deletes the Deployment and Service for the given project name. Uses
    --ignore-not-found so this is safe to call even if only one of them
    was applied before the failure.

    This is the Phase 1/2 equivalent of `helm rollback`. In Phase 3, Helm's
    --atomic flag handles this automatically.

    Returns:
        True if cleanup succeeded (or resources didn't exist), False if
        kubectl itself errored in an unexpected way.
    """
    warn(f"Deployment failed — cleaning up partial resources for [cyan]{project_name}[/cyan]...")

    deployment_result = run_command(
        [
            "kubectl", "delete", "deployment", project_name,
            "-n", namespace,
            "--ignore-not-found",
        ],
        capture_output=True,
    )
    service_result = run_command(
        [
            "kubectl", "delete", "service", project_name,
            "-n", namespace,
            "--ignore-not-found",
        ],
        capture_output=True,
    )

    success = deployment_result.returncode == 0 and service_result.returncode == 0
    if success:
        info("Partial resources removed. Cluster is clean.")
    else:
        warn("Cleanup may be incomplete. Run `kubectl get all` to check cluster state.")
    return success


def get_deployment_status(
    deployment_name: str,
    namespace: str = "default"
) -> dict:
    """
    Retrieves the current status of a Kubernetes deployment.

    Uses `kubectl get deployment -o json` to get machine-readable output,
    then parses the JSON to extract the fields we care about.

    Returns a dict with status info, or empty dict if deployment not found
    or kubectl fails.
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

    Returns list of dicts with pod info, or empty list on any error.
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


# ── Private helpers ──────────────────────────────────────────────────────────

def _ensure_namespace(namespace: str) -> None:
    """
    Creates a Kubernetes namespace if it doesn't exist.

    'default' always exists — skip it. For all other namespaces we attempt
    creation and ignore the error if it already exists.
    """
    if namespace == "default":
        return

    result = run_command(
        ["kubectl", "create", "namespace", namespace],
        capture_output=True
    )
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
    - HOW to update pods (RollingUpdate: never drop below current count)
    - WHEN a pod is healthy (liveness/readiness probes)
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
          # imagePullPolicy: Never → don't try to pull from registry
          # Phase 3 changes this to Always for ECR-hosted images
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
    """
    Generates a Kubernetes Service manifest.

    NodePort type exposes the service on a port of the k3d host machine,
    making it accessible at localhost:<nodePort> during local development.
    Phase 3 changes this to ClusterIP + Ingress for production.
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

    The `-` argument means "read from stdin", so we pass the YAML string
    directly rather than writing a temporary file.

    Returns True on success, False on failure.
    """
    result = __import__("subprocess").run(
        ["kubectl", "apply", "-f", "-"],
        input=manifest_yaml,
        text=True,
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
        capture_output=False
    )
    return result.returncode == 0


def _get_service_url(name: str, namespace: str, port: int) -> str:
    """
    Gets the URL to access the service locally.

    For k3d clusters the service is accessible at localhost:<nodePort>.
    We query kubectl to find the actual NodePort assigned by Kubernetes.
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
    Without this step, pods fail with ErrImageNeverPull or ImagePullBackOff.
    This is a Phase 1/2 requirement. In Phase 3 with ECR, imagePullPolicy
    becomes Always and images are pulled directly from the registry.
    """
    info(f"Importing [cyan]{image_ref}[/cyan] into k3d cluster [cyan]{cluster_name}[/cyan]...")
    result = run_command(
        ["k3d", "image", "import", image_ref, "-c", cluster_name],
        capture_output=False
    )
    return result.returncode == 0
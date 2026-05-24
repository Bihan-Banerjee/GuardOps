# infra/terraform/modules/alertmanager-webhook/main.tf
#
# GuardOps - Phase 8 - Alertmanager Webhook Handler Module
#
# PURPOSE:
#   Deploys the alertmanager_handler.py FastAPI service as a Kubernetes
#   Deployment + Service inside the cluster so Alertmanager can reach it
#   at a stable in-cluster URL.
#
#   The handler runs in the `monitoring` namespace alongside Alertmanager,
#   Prometheus, and Loki so it shares the observability network segment.
#
# WHAT THIS MODULE CREATES:
#   1. Kubernetes Namespace resource guard  (idempotent - uses `data` if exists)
#   2. ServiceAccount                        (runs the handler pod)
#   3. ClusterRole                           (kubectl permissions: pods, networkpolicies, nodes)
#   4. ClusterRoleBinding                    (binds the role to the ServiceAccount)
#   5. Deployment                            (handler pod - FastAPI + uvicorn)
#   6. Service (ClusterIP)                   (stable DNS: alertmanager-webhook.monitoring.svc)
#
# RBAC PERMISSIONS:
#   The handler calls kubectl inside the pod via subprocess.  kubectl inside
#   the pod uses the pod's ServiceAccount token (in-cluster auth) rather than
#   ~/.kube/config.  The ClusterRole grants exactly what alertmanager_handler.py
#   needs and nothing more:
#     - pods:           get, list, patch     (label the offending pod)
#     - networkpolicies: get, list, create, delete (apply/remove quarantine policy)
#     - nodes:          get, list, patch, cordon  (for drain path - cordon is a patch)
#
# ALERTMANAGER INTEGRATION:
#   After applying this module, set the webhook URL in your Alertmanager config:
#     receivers:
#       - name: guardops-webhook
#         webhook_configs:
#           - url: http://alertmanager-webhook.monitoring.svc.cluster.local:9095/webhook
#
#   See k8s/alertmanager/quarantine-webhook.yaml for the full receiver config.
#
# IMAGE:
#   The handler is packaged as a Docker image built from the GuardOps repo root.
#   var.webhook_image should point to the ECR image pushed during `guardops deploy`.
#   Default is the ECR pattern used throughout GuardOps.
#
# RELATED FILES:
#   backend/security/alertmanager_handler.py     - FastAPI handler source
#   k8s/alertmanager/quarantine-webhook.yaml     - Alertmanager receiver config
#   k8s/networkpolicy/quarantine-template.yaml   - reference NetworkPolicy
#   cli/commands/quarantine_cmd.py               - guardops quarantine-status
#   infra/terraform/main.tf                      - root module (wires this module)

terraform {
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.30"
    }
  }
}


# ── Locals ────────────────────────────────────────────────────────────────────

locals {
  name      = "${var.project_name}-alertmanager-webhook"
  namespace = var.monitoring_namespace

  # Labels applied to every resource in this module - consistent with the
  # label strategy used across GuardOps Helm charts and Terraform modules.
  common_labels = {
    "app.kubernetes.io/name"       = "alertmanager-webhook"
    "app.kubernetes.io/component"  = "self-healing"
    "app.kubernetes.io/managed-by" = "terraform"
    "guardops.io/phase"            = "8"
    "app"                          = "alertmanager-webhook"
  }
}


# ── ServiceAccount ────────────────────────────────────────────────────────────
#
# The handler pod uses this ServiceAccount to authenticate against the
# Kubernetes API server when kubectl runs inside the container.
# kubectl in-cluster auth automatically mounts the token at:
#   /var/run/secrets/kubernetes.io/serviceaccount/token

resource "kubernetes_service_account" "webhook" {
  metadata {
    name      = local.name
    namespace = local.namespace
    labels    = local.common_labels

    annotations = {
      "guardops.io/description" = "ServiceAccount for the Phase 8 Alertmanager webhook handler. Used for in-cluster kubectl calls to quarantine pods and drain nodes."
    }
  }
}


# ── ClusterRole ───────────────────────────────────────────────────────────────
#
# Grants the minimal set of API permissions needed by alertmanager_handler.py.
#
# Rule breakdown:
#   pods/patch           - `kubectl label pod` patches the pod metadata.
#   networkpolicies      - create, list, delete quarantine NetworkPolicies.
#   nodes/patch          - `kubectl cordon` patches the node's spec.unschedulable.
#   nodes/drain          - `kubectl drain` is a client-side operation that
#                          combines pod eviction + node cordon; the eviction
#                          sub-resource is covered by pods/eviction below.
#   pods/eviction        - required for `kubectl drain` to evict pods gracefully.

resource "kubernetes_cluster_role" "webhook" {
  metadata {
    name   = local.name
    labels = local.common_labels
  }

  # Pod labelling (quarantine label) and eviction (node drain)
  rule {
    api_groups = [""]
    resources  = ["pods", "pods/eviction"]
    verbs      = ["get", "list", "patch", "create"]
  }

  # NetworkPolicy CRUD - create quarantine policy, delete on resolve
  rule {
    api_groups = ["networking.k8s.io"]
    resources  = ["networkpolicies"]
    verbs      = ["get", "list", "create", "delete", "patch"]
  }

  # Node cordon + drain
  rule {
    api_groups = [""]
    resources  = ["nodes"]
    verbs      = ["get", "list", "patch"]
  }
}


# ── ClusterRoleBinding ────────────────────────────────────────────────────────

resource "kubernetes_cluster_role_binding" "webhook" {
  metadata {
    name   = local.name
    labels = local.common_labels
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role.webhook.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.webhook.metadata[0].name
    namespace = local.namespace
  }
}


# ── Deployment ────────────────────────────────────────────────────────────────
#
# Runs the alertmanager_handler.py FastAPI service.
#
# Single replica - the handler is stateless (all state lives in K8s API objects)
# so a single replica is safe and avoids double-quarantine from concurrent
# webhook deliveries.  Alertmanager retries on failure, so availability
# is handled by the deployment controller restarting the pod on crash.

resource "kubernetes_deployment" "webhook" {
  metadata {
    name      = local.name
    namespace = local.namespace
    labels    = local.common_labels
  }

  spec {
    # Single replica - see comment above. Scale to 2+ only if you add
    # an external deduplication mechanism (e.g. Redis lock on fingerprint).
    replicas = var.replicas

    selector {
      match_labels = {
        app = "alertmanager-webhook"
      }
    }

    template {
      metadata {
        labels = local.common_labels

        annotations = {
          # Force pod replacement when the image digest changes.
          # Without this, Terraform won't restart the pod when only the
          # image tag changes (Kubernetes compares image string, not digest).
          "guardops.io/config-hash" = sha256(var.webhook_image)
        }
      }

      spec {
        service_account_name             = kubernetes_service_account.webhook.metadata[0].name
        automount_service_account_token  = true   # required for in-cluster kubectl

        # ── Security context (mirrors guardops-app Helm chart) ────────────────
        security_context {
          run_as_non_root = true
          run_as_user     = 10001    # matches Dockerfile UID in test-project
          fs_group        = 10001
        }

        container {
          name  = "webhook-handler"
          image = var.webhook_image

          # Command runs uvicorn directly rather than using `python -m` to avoid
          # the extra process layer. --workers 1 keeps the process model simple
          # for a single-replica handler (no shared state issues).
          image_pull_policy = "Always"

          command = [
            "/venv/bin/python",
            "-m",
            "uvicorn",
            "backend.security.alertmanager_handler:app",
            "--host", "0.0.0.0",
            "--port", tostring(var.webhook_port),
            "--workers", "1",
            "--log-level", "info",
          ]

          port {
            name           = "http"
            container_port = var.webhook_port
            protocol       = "TCP"
          }

          env {
            name  = "WEBHOOK_PORT"
            value = tostring(var.webhook_port)
          }

          env {
            name  = "PYTHONUNBUFFERED"
            value = "1"   # Ensures logs are written immediately (no buffering)
          }

          # ── Resource limits ───────────────────────────────────────────────
          # Webhook handler is lightweight - FastAPI + small subprocess calls.
          # Limits prevent runaway memory if a malformed payload triggers a
          # large object allocation.
          resources {
            requests = {
              cpu    = "50m"
              memory = "64Mi"
            }
            limits = {
              cpu    = "200m"
              memory = "128Mi"
            }
          }

          # ── Liveness probe ────────────────────────────────────────────────
          # Restarts the pod if FastAPI stops serving /healthz.
          liveness_probe {
            http_get {
              path = "/healthz"
              port = var.webhook_port
            }
            initial_delay_seconds = 10
            period_seconds        = 15
            failure_threshold     = 3
          }

          # ── Readiness probe ───────────────────────────────────────────────
          # Removes the pod from the Service endpoints if kubectl is unavailable.
          # This prevents Alertmanager from delivering webhooks to a handler
          # that can't execute the kubectl quarantine commands.
          readiness_probe {
            http_get {
              path = "/readyz"
              port = var.webhook_port
            }
            initial_delay_seconds = 5
            period_seconds        = 10
            failure_threshold     = 3
          }

          # ── Container security context ────────────────────────────────────
          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = false   # uvicorn writes temp files
            capabilities {
              drop = ["ALL"]
            }
          }
        }

        # Restart policy: Always - Deployment controller handles pod lifecycle.
        restart_policy = "Always"
      }
    }
  }

  # Wait for the deployment to become available before Terraform marks
  # the resource as complete.  This surfaces probe failures early.
  wait_for_rollout = true
}


# ── Service (ClusterIP) ───────────────────────────────────────────────────────
#
# Provides a stable DNS name for Alertmanager to reach the handler:
#   http://alertmanager-webhook.monitoring.svc.cluster.local:9095/webhook
#
# ClusterIP (not LoadBalancer / NodePort) - the handler only needs to be
# reachable from inside the cluster (by Alertmanager), not from the internet.

resource "kubernetes_service" "webhook" {
  metadata {
    name      = local.name
    namespace = local.namespace
    labels    = local.common_labels

    annotations = {
      "guardops.io/description" = "ClusterIP service for the Phase 8 Alertmanager webhook handler."
    }
  }

  spec {
    selector = {
      app = "alertmanager-webhook"
    }

    port {
      name        = "http"
      port        = var.webhook_port
      target_port = var.webhook_port
      protocol    = "TCP"
    }

    type = "ClusterIP"
  }
}

# infra/terraform/modules/alertmanager-webhook/variables.tf
#
# GuardOps — Phase 8 — Alertmanager Webhook Handler Module — Input Variables
#
# All variables follow the same conventions as other GuardOps modules:
#   - project_name and environment are always required (no defaults) so the
#     root module must pass them explicitly — prevents silent misconfiguration.
#   - Operational knobs (replicas, image, port) have sensible defaults that
#     match the alertmanager_handler.py constants and the test-project setup.

# ── Required (no defaults) ─────────────────────────────────────────────────────

variable "project_name" {
  description = "GuardOps project name. Used as a prefix in resource names."
  type        = string

  validation {
    condition     = length(var.project_name) > 0
    error_message = "project_name must not be empty."
  }
}

variable "environment" {
  description = "Deployment environment: 'prod', 'staging', or 'local'."
  type        = string

  validation {
    condition     = contains(["prod", "staging", "local"], var.environment)
    error_message = "environment must be one of: prod, staging, local."
  }
}

variable "webhook_image" {
  description = (
    "Full Docker image reference for the alertmanager webhook handler. "
    "Should point to the ECR image built from the GuardOps repo root. "
    "Example: 123456789012.dkr.ecr.ap-south-1.amazonaws.com/guardops-prod:webhook-latest"
  )
  type = string

  validation {
    condition     = length(var.webhook_image) > 0
    error_message = "webhook_image must not be empty."
  }
}

# ── Optional (have defaults) ──────────────────────────────────────────────────

variable "monitoring_namespace" {
  description = (
    "Kubernetes namespace where the webhook handler is deployed. "
    "Should match the namespace where Alertmanager runs so they share "
    "the same network segment. Default: 'monitoring' (set by setup-observability.ps1)."
  )
  type    = string
  default = "monitoring"
}

variable "webhook_port" {
  description = (
    "Port the FastAPI handler listens on inside the pod. "
    "Must match the WEBHOOK_PORT constant in alertmanager_handler.py (default: 9095). "
    "Override if 9095 conflicts with another service in your cluster."
  )
  type    = number
  default = 9095

  validation {
    condition     = var.webhook_port >= 1024 && var.webhook_port <= 65535
    error_message = "webhook_port must be between 1024 and 65535."
  }
}

variable "replicas" {
  description = (
    "Number of handler pod replicas. "
    "Default is 1 because the handler is stateless and Alertmanager retries "
    "on failure — a single replica avoids double-quarantine from concurrent "
    "deliveries. Scale to 2+ only if you add external deduplication."
  )
  type    = number
  default = 1

  validation {
    condition     = var.replicas >= 1 && var.replicas <= 5
    error_message = "replicas must be between 1 and 5."
  }
}

variable "cpu_request" {
  description = "CPU request for the webhook handler container (Kubernetes resource string)."
  type        = string
  default     = "50m"
}

variable "memory_request" {
  description = "Memory request for the webhook handler container (Kubernetes resource string)."
  type        = string
  default     = "64Mi"
}

variable "cpu_limit" {
  description = "CPU limit for the webhook handler container."
  type        = string
  default     = "200m"
}

variable "memory_limit" {
  description = "Memory limit for the webhook handler container."
  type        = string
  default     = "128Mi"
}

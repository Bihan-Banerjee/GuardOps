# infra/terraform/modules/alertmanager-webhook/variables.tf
#
# GuardOps - Phase 8 - Alertmanager Webhook Handler Module - Input Variables

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
  description = "Full Docker image reference for the alertmanager webhook handler. Example: 123456789012.dkr.ecr.ap-south-1.amazonaws.com/guardops-prod:webhook-latest"
  type        = string

  validation {
    condition     = length(var.webhook_image) > 0
    error_message = "webhook_image must not be empty."
  }
}

# ── Optional (have defaults) ──────────────────────────────────────────────────

variable "monitoring_namespace" {
  description = "Kubernetes namespace where the webhook handler is deployed. Should match the namespace where Alertmanager runs. Default: 'monitoring'."
  type        = string
  default     = "monitoring"
}

variable "webhook_port" {
  description = "Port the FastAPI handler listens on. Must match WEBHOOK_PORT in alertmanager_handler.py (default: 9095)."
  type        = number
  default     = 9095

  validation {
    condition     = var.webhook_port >= 1024 && var.webhook_port <= 65535
    error_message = "webhook_port must be between 1024 and 65535."
  }
}

variable "replicas" {
  description = "Number of handler pod replicas. Default is 1 - handler is stateless and Alertmanager retries on failure. Scale to 2+ only with external deduplication."
  type        = number
  default     = 1

  validation {
    condition     = var.replicas >= 1 && var.replicas <= 5
    error_message = "replicas must be between 1 and 5."
  }
}

variable "cpu_request" {
  description = "CPU request for the webhook handler container."
  type        = string
  default     = "50m"
}

variable "memory_request" {
  description = "Memory request for the webhook handler container."
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

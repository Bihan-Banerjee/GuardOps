# infra/terraform/modules/falco/variables.tf

variable "project_name" {
  description = "Project name — used for resource tagging"
  type        = string
}

variable "environment" {
  description = "Environment name (prod, staging)"
  type        = string
}

variable "monitoring_namespace" {
  description = "Kubernetes namespace where monitoring components are installed. Must already exist."
  type        = string
  default     = "monitoring"
}

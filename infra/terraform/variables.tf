# infra/terraform/variables.tf — Phase 5

variable "aws_account_id" {
  description = "Your 12-digit AWS account ID."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be exactly 12 digits."
  }
}

variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "project_name" {
  type    = string
  default = "guardops"
}

variable "enable_runtime_security" {
  description = "Install Falco + Loki + Promtail via Helm. Set false on first apply (EKS must exist first), then true once the cluster is running."
  type        = bool
  default     = false
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "ecr_image_names" {
  type    = list(string)
  default = ["guardops-app"]
}

# ── VPC ───────────────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "ONE AZ = one NAT gateway = minimum cost (~$32/month saved vs two AZs)."
  type        = list(string)
  default     = ["ap-south-1a"]
}

# ── EKS cost controls ─────────────────────────────────────────────────────────

variable "node_instance_type" {
  description = <<-EOT
    PHASE 5 CHANGE: upgraded from t3.medium (4GB) to t3.large (8GB).
    Prometheus (~500MB) + Grafana (~300MB) + Alertmanager (~100MB) + kube-state-metrics (~100MB)
    adds ~1GB overhead on top of the existing stack. t3.medium OOMs under this load.
    t3.large costs ~$0.075/hr vs $0.036/hr for t3.medium — about $0.94/day extra.
    Still destroy every night to keep costs manageable.
  EOT
  type        = string
  default     = "t3.large"
}

variable "node_min_size" {
  type    = number
  default = 1
}

variable "node_max_size" {
  type    = number
  default = 1
}

variable "enable_self_healing" {
  description = "Phase 8: deploy the Alertmanager webhook handler."
  type        = bool
  default     = false
}

variable "webhook_image" {
  description = "Full ECR image reference for the alertmanager webhook handler."
  type        = string
  default     = ""
}
variable "node_desired_size" {
  type    = number
  default = 1
}

variable "enable_cloudwatch_logs" {
  description = "Disable during dev to avoid CloudWatch costs."
  type        = bool
  default     = false
}

variable "github_repo" {
  description = <<-EOT
    GitHub repository that is allowed to assume the CI role via OIDC.
    Format: "owner/repo" — e.g. "Bihan-Banerjee/GuardOps"
  EOT
  type        = string
  default     = "Bihan-Banerjee/GuardOps"
}

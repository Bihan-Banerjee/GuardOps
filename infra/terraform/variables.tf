# infra/terraform/variables.tf
#
# GuardOps - Root Module Input Variables
#
# Phase 8 additions:
#   - enable_self_healing  (gate for alertmanager-webhook module)
#   - webhook_image        (ECR image for the handler pod)

# ── AWS ───────────────────────────────────────────────────────────────────────

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "ap-south-1"
}

variable "aws_account_id" {
  description = "AWS account ID. Used to restrict the AWS provider to one account."
  type        = string
}

# ── Project ───────────────────────────────────────────────────────────────────

variable "project_name" {
  description = "Short project name used as a prefix for all resource names."
  type        = string
  default     = "guardops"
}

variable "environment" {
  description = "Deployment environment: 'prod', 'staging', or 'local'."
  type        = string
  default     = "prod"
}

# ── Networking ────────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "List of AZs to create subnets in. Two AZs minimum for EKS."
  type        = list(string)
  default     = ["ap-south-1a", "ap-south-1b"]
}

# ── ECR ───────────────────────────────────────────────────────────────────────

variable "ecr_image_names" {
  description = "List of ECR repository names to create."
  type        = list(string)
  default     = ["guardops-prod"]
}

# ── EKS ───────────────────────────────────────────────────────────────────────

variable "node_instance_type" {
  description = "EC2 instance type for EKS worker nodes."
  type        = string
  default     = "t3.large"
}

variable "node_min_size" {
  description = "Minimum number of nodes in the EKS managed node group."
  type        = number
  default     = 1
}

variable "node_max_size" {
  description = "Maximum number of nodes in the EKS managed node group."
  type        = number
  default     = 3
}

variable "node_desired_size" {
  description = "Desired number of nodes in the EKS managed node group."
  type        = number
  default     = 1
}

variable "enable_cloudwatch_logs" {
  description = "Enable EKS control plane logging to CloudWatch."
  type        = bool
  default     = false
}

# ── GitHub OIDC (Phase 6) ─────────────────────────────────────────────────────

variable "github_repo" {
  description = "GitHub repo in owner/name format. Used to scope the OIDC trust policy. Example: Bihan-Banerjee/GuardOps"
  type        = string
}

# ── Runtime Security (Phase 7) ────────────────────────────────────────────────

variable "enable_runtime_security" {
  description = "Deploy Falco + Loki + Promtail via the falco Terraform module. Requires a live EKS cluster - apply AWS modules first, then set this to true."
  type        = bool
  default     = false
}

# ── Self-Healing (Phase 8) ────────────────────────────────────────────────────

variable "enable_self_healing" {
  description = "Deploy the Alertmanager webhook handler for automated pod quarantine. Requires enable_runtime_security = true and webhook_image to be set."
  type        = bool
  default     = false
}

variable "webhook_image" {
  description = "Full ECR image reference for the alertmanager webhook handler. Example: 236796665744.dkr.ecr.ap-south-1.amazonaws.com/guardops-prod:webhook-latest"
  type        = string
  default     = ""
}

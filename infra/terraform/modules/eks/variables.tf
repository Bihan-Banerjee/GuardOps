# infra/terraform/modules/eks/variables.tf

variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "cluster_role_arn" {
  description = "ARN of the EKS cluster IAM role. From iam module output."
  type        = string
}

variable "node_role_arn" {
  description = "ARN of the EKS node IAM role. From iam module output."
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for nodes. From vpc module output."
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "List of public subnet IDs for load balancers. From vpc module output."
  type        = list(string)
}

# ── Cost control variables ────────────────────────────────────────────────────
# Defaults are set for minimum cost (single t3.medium node).
# t3.medium is the minimum practical size for EKS — t3.small often OOMs.

variable "node_instance_type" {
  description = "EC2 instance type for worker nodes. t3.medium = ~$0.04/hr."
  type        = string
  default     = "t3.medium"
}

variable "node_min_size" {
  description = "Minimum number of nodes. Keep at 1 for Phase 4B testing."
  type        = number
  default     = 1
}

variable "node_max_size" {
  description = "Maximum number of nodes."
  type        = number
  default     = 1
}

variable "node_desired_size" {
  description = "Desired number of nodes."
  type        = number
  default     = 1
}

variable "enable_cloudwatch_logs" {
  description = "Enable EKS control plane CloudWatch logging. Costs ~$0.50/GB. Disable for dev."
  type        = bool
  default     = false  # disabled to save cost — enable for prod
}

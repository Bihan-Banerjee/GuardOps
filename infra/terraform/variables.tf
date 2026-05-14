# infra/terraform/variables.tf — Phase 4B

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
  description = "t3.medium = minimum viable for EKS. t3.small often OOMs."
  type        = string
  default     = "t3.medium"
}

variable "node_min_size" {
  type    = number
  default = 1
}

variable "node_max_size" {
  type    = number
  default = 1
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

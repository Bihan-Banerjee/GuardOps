# infra/terraform/variables.tf
#
# Root module variables — Phase 4A
# All values are set in terraform.tfvars (never commit that file).

# ── Required ──────────────────────────────────────────────────────────────────

variable "aws_account_id" {
  description = "Your 12-digit AWS account ID. Used as a safety check to prevent applying to wrong account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be exactly 12 digits."
  }
}

# ── Optional with defaults ────────────────────────────────────────────────────

variable "aws_region" {
  description = "AWS region to deploy into. ap-south-1 = Mumbai (closest + cheapest for India)."
  type        = string
  default     = "ap-south-1"
}

variable "project_name" {
  description = "Short project identifier. Used as a prefix on all resource names."
  type        = string
  default     = "guardops"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "ecr_image_names" {
  description = "List of ECR repository names to create. Add more if you add services."
  type        = list(string)
  default     = ["guardops-app"]
}

# ── PHASE 4B variables — leave as-is until you uncomment the EKS module ──────

variable "vpc_cidr" {
  description = "PHASE 4B: CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "PHASE 4B: AZs to spread subnets across. Use 2 for dev cost savings, 3 for prod HA."
  type        = list(string)
  default     = ["ap-south-1a", "ap-south-1b"]
}

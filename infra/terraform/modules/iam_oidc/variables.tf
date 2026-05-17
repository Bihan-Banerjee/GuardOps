# infra/terraform/modules/iam_oidc/variables.tf

variable "project_name" {
  description = "Project name — used as prefix for all resource names"
  type        = string
}

variable "environment" {
  description = "Deployment environment (prod, staging, etc.)"
  type        = string
}

variable "aws_region" {
  description = "AWS region — used to scope IAM policy ARNs"
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID — used to scope IAM policy ARNs"
  type        = string
}

variable "github_repo" {
  description = <<-EOT
    GitHub repository in 'owner/repo' format.
    Used to scope the OIDC trust condition so only your repo can assume the role.
    Example: "Bihan-Banerjee/GuardOps"
  EOT
  type        = string
}

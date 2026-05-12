# infra/terraform/modules/ecr/variables.tf

variable "project_name" {
  description = "Project name prefix for resource tags."
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev / staging / prod)."
  type        = string
}

variable "aws_region" {
  description = "AWS region where ECR lives."
  type        = string
}

variable "image_names" {
  description = "List of ECR repository names to create."
  type        = list(string)
  default     = ["guardops-app"]
}

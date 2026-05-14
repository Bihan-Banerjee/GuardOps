# infra/terraform/modules/s3/variables.tf

variable "project_name" {
  description = "Project name prefix. Used in bucket name."
  type        = string
}

variable "environment" {
  description = "Deployment environment."
  type        = string
}

variable "aws_region" {
  description = "AWS region where the bucket lives."
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID. Appended to bucket name to guarantee global uniqueness."
  type        = string
}

# infra/terraform/variables.tf

variable "project_name" {
  description = "Project identifier used in all resource names and tags"
  type        = string
  default     = "guardops"
}

variable "environment" {
  description = "Deployment environment: dev, staging, prod"
  type        = string
  default     = "prod"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "aws_account_id" {
  description = "12-digit AWS account ID (used in IAM policies)"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones to spread nodes and subnets across"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

variable "node_instance_type" {
  description = "EC2 instance type for EKS worker nodes"
  type        = string
  default     = "t3.medium"   # 2 vCPU, 4GB RAM — minimum for running Prometheus stack
}

variable "node_min_size" {
  description = "Minimum number of EKS worker nodes"
  type        = number
  default     = 1
}

variable "node_max_size" {
  description = "Maximum number of EKS worker nodes (HPA scales pods, CA scales nodes)"
  type        = number
  default     = 4
}

variable "node_desired_size" {
  description = "Desired number of EKS worker nodes at steady state"
  type        = number
  default     = 2
}

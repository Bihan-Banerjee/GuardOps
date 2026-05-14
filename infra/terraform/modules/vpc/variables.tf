# infra/terraform/modules/vpc/variables.tf

variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "AZs to use. ONE AZ = one NAT gateway = minimum cost. Add more for HA."
  type        = list(string)
  default     = ["ap-south-1a"]  # single AZ saves ~$32/month vs two AZs
}

# infra/terraform/main.tf
#
# GuardOps — Root Terraform Configuration
#
# PHASE 4A (current): Creates ECR + S3 only. FREE / near-free.
#   - ECR: free up to 500 MB storage
#   - S3:  free up to 5 GB storage
#
# PHASE 4B (future, costs money): Uncomment VPC + IAM + EKS modules below.
#   Estimated cost: ~$200/month. Always `terraform destroy` after testing.
#
# Usage:
#   terraform init
#   terraform plan
#   terraform apply
#   terraform destroy   ← run this when done to avoid charges

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # PHASE 4B: Uncomment this backend block AFTER first apply has created the S3 bucket.
  # Then run `terraform init -migrate-state` to move state into S3.
  # Until then, state is stored locally in terraform.tfstate (do NOT commit this file).
  #
  # backend "s3" {
  #   bucket  = "guardops-terraform-state-<YOUR_ACCOUNT_ID>"
  #   key     = "guardops/terraform.tfstate"
  #   region  = "ap-south-1"
  #   encrypt = true
  # }
}

provider "aws" {
  region = var.aws_region

  # Safety net: prevents accidental apply to a wrong AWS account.
  # Fill in your 12-digit account ID in terraform.tfvars.
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ── PHASE 4A: Active modules ──────────────────────────────────────────────────

module "ecr" {
  source = "./modules/ecr"

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region
  image_names  = var.ecr_image_names
}

module "s3" {
  source = "./modules/s3"

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region
  aws_account_id = var.aws_account_id
}

# ── PHASE 4B: Expensive modules — keep commented until ready ─────────────────
#
# WARNING: Uncommenting these will immediately start incurring costs:
#   - NAT Gateway:     ~$32/month (charged per hour, even if idle)
#   - EKS control plane: ~$73/month
#   - EC2 nodes:       ~$60/month
#
# Steps to enable Phase 4B:
#   1. Uncomment the three modules below
#   2. Add the Phase 4B variables to terraform.tfvars
#   3. Run: terraform plan → review → terraform apply
#   4. After testing: terraform destroy  ← CRITICAL, do not forget this
#
# module "vpc" {
#   source = "./modules/vpc"
#
#   project_name       = var.project_name
#   environment        = var.environment
#   vpc_cidr           = var.vpc_cidr
#   availability_zones = var.availability_zones
# }
#
# module "iam" {
#   source = "./modules/iam"
#
#   project_name   = var.project_name
#   environment    = var.environment
#   aws_account_id = var.aws_account_id
# }
#
# module "eks" {
#   source = "./modules/eks"
#
#   project_name     = var.project_name
#   environment      = var.environment
#   aws_region       = var.aws_region
#   vpc_id           = module.vpc.vpc_id
#   private_subnets  = module.vpc.private_subnet_ids
#   public_subnets   = module.vpc.public_subnet_ids
#
#   # From IAM module outputs — Phase 4B
#   cluster_role_arn = module.iam.eks_cluster_role_arn
#   node_role_arn    = module.iam.eks_node_role_arn
# }

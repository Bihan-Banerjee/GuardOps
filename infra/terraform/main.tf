# infra/terraform/main.tf
#
# GuardOps — Root Terraform Configuration — Phase 4.5
#
# Changes from Phase 4B:
#   - S3 remote backend enabled (run bootstrap/ first, then terraform init -migrate-state)
#   - Cost comment updated for t3.large node (Phase 5 upgrade)
#
# Estimated cost when running:
#   EKS control plane : $0.10/hr  ($2.40/day)
#   t3.large node     : $0.075/hr ($1.80/day)   ← upgraded from t3.medium for Phase 5
#   NAT Gateway       : $0.045/hr ($1.08/day)   — single AZ
#   ECR + S3          : ~$0.00 (free tier)
#   ─────────────────────────────────────────────
#   TOTAL             : ~$5.28/day
#
# RULE: Run `terraform destroy` every evening. Never leave EKS overnight.
# NOTE: EBS volumes (Prometheus/Grafana PVCs) persist after destroy — see Phase 5 guide.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote state — S3 + DynamoDB locking.
  # PREREQUISITE: Run infra/terraform/bootstrap/ first to create this bucket and table.
  # MIGRATION:    After bootstrap, run `terraform init -migrate-state` once.
  # WHY:          Local tfstate loss = unrecoverable infrastructure. S3 versioning
  #               means every state revision is recoverable.
  backend "s3" {
    bucket         = "guardops-tfstate-236796665744"
    key            = "guardops/prod/terraform.tfstate"
    region         = "ap-south-1"
    dynamodb_table = "guardops-tf-lock"
    encrypt        = true
  }
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ── Always-on (free tier) ─────────────────────────────────────────────────────

module "ecr" {
  source = "./modules/ecr"

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region
  image_names  = var.ecr_image_names
}

module "s3" {
  source = "./modules/s3"

  project_name   = var.project_name
  environment    = var.environment
  aws_region     = var.aws_region
  aws_account_id = var.aws_account_id
}

# ── Phase 4B+ (costs money — destroy when not in use) ─────────────────────────

module "vpc" {
  source = "./modules/vpc"

  project_name       = var.project_name
  environment        = var.environment
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones
}

module "iam" {
  source = "./modules/iam"

  project_name   = var.project_name
  environment    = var.environment
  aws_region     = var.aws_region
  aws_account_id = var.aws_account_id
}

module "eks" {
  source = "./modules/eks"

  project_name       = var.project_name
  environment        = var.environment
  aws_region         = var.aws_region
  cluster_role_arn   = module.iam.eks_cluster_role_arn
  node_role_arn      = module.iam.eks_node_role_arn
  private_subnet_ids = module.vpc.private_subnet_ids
  public_subnet_ids  = module.vpc.public_subnet_ids

  node_instance_type     = var.node_instance_type
  node_min_size          = var.node_min_size
  node_max_size          = var.node_max_size
  node_desired_size      = var.node_desired_size
  enable_cloudwatch_logs = var.enable_cloudwatch_logs
}

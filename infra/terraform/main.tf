# infra/terraform/main.tf
#
# GuardOps — Root Terraform Configuration — Phase 7
#
# Changes from Phase 6:
#   - Added hashicorp/helm and hashicorp/kubernetes to required_providers
#   - Added data sources for EKS cluster auth (feeds the Helm provider)
#   - Added provider "helm" and provider "kubernetes" blocks
#   - Added falco module: Falco + Loki + Promtail (gated by enable_runtime_security)
#   - Added enable_runtime_security variable (default false for safe first apply)
#   - Added falco_loki_url output
#
# Changes from Phase 4.5:
#   - Added iam_oidc module: GitHub OIDC provider + CI role (no more static IAM keys)
#   - Added github_repo variable (required input — set in terraform.tfvars)
#   - Added github_actions_role_arn output (copy value → AWS_ROLE_ARN GitHub secret)
#
# Changes from Phase 4B:
#   - S3 remote backend enabled (run bootstrap/ first, then terraform init -migrate-state)
#   - Cost comment updated for t3.large node (Phase 5 upgrade)
#
# ── FIRST APPLY INSTRUCTIONS (Phase 7) ────────────────────────────────────────
#
# The Helm provider needs a live EKS cluster to resolve the data sources.
# On a fresh environment, do a two-phase apply:
#
#   Phase 1 — provision AWS infrastructure only:
#     terraform apply -target=module.vpc -target=module.iam -target=module.iam_oidc \
#                     -target=module.eks -target=module.ecr -target=module.s3
#
#   Phase 2 — install runtime security stack (EKS must be running):
#     # First install kube-prometheus-stack manually (creates `monitoring` namespace):
#     .\scripts\setup-observability.ps1
#     # Then apply the falco module:
#     terraform apply   # enable_runtime_security = true in terraform.tfvars
#
# Or use the script-only approach (no phase 2 Terraform needed):
#   .\scripts\setup-runtime-security.ps1
#   (Sets monitoring.loki_url in .guardops.yaml automatically)
#
# Estimated cost when running (unchanged from Phase 6):
#   EKS control plane : $0.10/hr  ($2.40/day)
#   t3.large node     : $0.075/hr ($1.80/day)
#   NAT Gateway       : $0.045/hr ($1.08/day)   — single AZ
#   ECR + S3          : ~$0.00    (free tier)
#   ─────────────────────────────────────────────
#   TOTAL             : ~$5.28/day
#
# RULE: Run `terraform destroy` every evening. Never leave EKS overnight.
# NOTE: Delete Loki PVC before destroy — same as Prometheus/Grafana PVCs.
#   kubectl delete pvc --all -n monitoring

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    # Phase 7: Helm and Kubernetes providers for Falco/Loki/Promtail installation.
    # These require a live EKS cluster — see FIRST APPLY INSTRUCTIONS above.
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.13"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.30"
    }
  }

  # Remote state — S3 + DynamoDB locking.
  # PREREQUISITE: Run infra/terraform/bootstrap/ first to create this bucket and table.
  # MIGRATION:    After bootstrap, run `terraform init -migrate-state` once.
  backend "s3" {
    bucket         = "guardops-tfstate-236796665744"
    key            = "guardops/prod/terraform.tfstate"
    region         = "ap-south-1"
    use_lockfile   = true
    encrypt        = true
  }
}

# ── AWS provider ──────────────────────────────────────────────────────────────

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

# ── Phase 7: EKS auth data sources (feed Helm + Kubernetes providers) ─────────
#
# These data sources read the live EKS cluster endpoint and generate a
# short-lived auth token. Terraform re-fetches the token on each apply
# (tokens expire after 15 minutes).
#
# depends_on = [module.eks] ensures Terraform provisions EKS before trying
# to read the cluster. Without this, a fresh `terraform apply` would fail
# because the cluster doesn't exist when the data source is evaluated.

data "aws_eks_cluster" "guardops" {
  name       = "${var.project_name}-${var.environment}-cluster"
  depends_on = [module.eks]
}

data "aws_eks_cluster_auth" "guardops" {
  name       = "${var.project_name}-${var.environment}-cluster"
  depends_on = [module.eks]
}

# ── Helm provider ─────────────────────────────────────────────────────────────

provider "helm" {
  kubernetes {
    host                   = data.aws_eks_cluster.guardops.endpoint
    cluster_ca_certificate = base64decode(
      data.aws_eks_cluster.guardops.certificate_authority[0].data
    )
    token = data.aws_eks_cluster_auth.guardops.token
  }
}

# ── Kubernetes provider ───────────────────────────────────────────────────────

provider "kubernetes" {
  host                   = data.aws_eks_cluster.guardops.endpoint
  cluster_ca_certificate = base64decode(
    data.aws_eks_cluster.guardops.certificate_authority[0].data
  )
  token = data.aws_eks_cluster_auth.guardops.token
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

# ── Phase 6: GitHub OIDC (replaces CI IAM user) ───────────────────────────────
#
# After `terraform apply`:
#   1. Get the role ARN:  terraform output github_actions_role_arn
#   2. Add it as a GitHub secret:
#        gh secret set AWS_ROLE_ARN --body "<arn>"
#   3. Delete the old IAM secrets from GitHub:
#        gh secret delete AWS_ACCESS_KEY_ID
#        gh secret delete AWS_SECRET_ACCESS_KEY

module "iam_oidc" {
  source = "./modules/iam_oidc"

  project_name   = var.project_name
  environment    = var.environment
  aws_region     = var.aws_region
  aws_account_id = var.aws_account_id
  github_repo    = var.github_repo
}

output "github_actions_role_arn" {
  description = "Copy this value → GitHub secret AWS_ROLE_ARN"
  value       = module.iam_oidc.github_actions_role_arn
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

# ── Phase 7: Runtime Security (Falco + Loki + Promtail) ──────────────────────
#
# Gated by enable_runtime_security variable (default: false).
#
# WHY THE GATE:
#   The helm provider data sources require a live EKS cluster.
#   On a fresh environment, apply all other modules first, install
#   kube-prometheus-stack (creates the `monitoring` namespace), then
#   set enable_runtime_security = true and apply again.
#
# ALTERNATIVE (no Terraform needed):
#   Run .\scripts\setup-runtime-security.ps1 after the morning apply.
#   This installs Falco/Loki/Promtail via Helm directly and patches
#   .guardops.yaml with the Loki URL.
#
# IMPORTANT: Add Loki PVC to nightly shutdown before terraform destroy:
#   kubectl delete pvc --all -n monitoring   (already in night-shutdown.ps1)

module "falco" {
  source = "./modules/falco"
  count  = var.enable_runtime_security ? 1 : 0

  project_name         = var.project_name
  environment          = var.environment
  monitoring_namespace = "monitoring"

  depends_on = [module.eks]
}

output "falco_loki_url" {
  description = "In-cluster Loki URL — set as monitoring.loki_url in .guardops.yaml"
  value = (
    var.enable_runtime_security
    ? module.falco[0].loki_service_url
    : "Runtime security not enabled — set enable_runtime_security = true"
  )
}

# infra/terraform/main.tf
#
# GuardOps — Root Terraform Configuration — Phase 10
#
# Changes from Phase 8:
#   - Added dns-tls module: Route53 + cert-manager + AWS Load Balancer Controller
#     (gated by enable_dns_tls variable, default false — same gate pattern as Phase 7/8)
#   - Added argocd module: ArgoCD Helm release + AppProject + prod/staging Applications
#     (gated by enable_argocd variable, default false)
#   - Added enable_dns_tls, enable_argocd, domain_name, git_repo_url variables
#   - Added alb_controller_role_arn, alb_dns_name variables for dns-tls module
#   - Added outputs: name_servers, argocd_server_url, argocd_initial_password_cmd
#
# Changes from Phase 8:
#   - Added alertmanager_webhook module: FastAPI handler pod + RBAC
#   - Added enable_self_healing variable
#   - Added webhook_image variable
#
# ── FIRST APPLY INSTRUCTIONS (Phase 10) ───────────────────────────────────────
#
# Same two-phase approach as Phase 8, with two additional steps for TLS + GitOps:
#
#   Phase 1 — provision AWS infrastructure only (unchanged):
#     terraform apply -target=module.vpc -target=module.iam -target=module.iam_oidc \
#                     -target=module.eks -target=module.ecr -target=module.s3
#
#   Phase 2 — observability + runtime security + self-healing (unchanged):
#     .\scripts\setup-observability.ps1
#     .\scripts\setup-runtime-security.ps1
#     terraform apply   # enable_runtime_security=true, enable_self_healing=true
#
#   Phase 3 — TLS + DNS (new in Phase 10):
#     # Set in terraform.tfvars:
#     #   enable_dns_tls = true
#     #   domain_name    = "guardops.dev"
#     terraform apply -target=module.dns_tls
#     # After apply: delegate NS records at your domain registrar
#     terraform output name_servers
#     # Apply ClusterIssuers (cert-manager CRDs must be installed first):
#     kubectl apply -f k8s/tls/clusterissuer-letsencrypt-staging.yaml
#     kubectl apply -f k8s/tls/clusterissuer-letsencrypt-prod.yaml
#
#   Phase 4 — ArgoCD (new in Phase 10):
#     # Set in terraform.tfvars:
#     #   enable_argocd = true
#     #   git_repo_url  = "https://github.com/Bihan-Banerjee/GuardOps"
#     terraform apply -target=module.argocd
#     # After apply: get admin password and create API token
#     terraform output argocd_initial_password_cmd   # copy and run it
#     # Log in: argocd login argocd.guardops.dev
#     # Generate CI token: argocd account generate-token --account admin
#     # Add as GitHub secret: gh secret set ARGOCD_TOKEN --body "<token>"
#     # Update .guardops.yaml with argocd.url from: terraform output argocd_server_url
#
#   Phase 5 — ALB DNS wiring (after first Helm deploy creates the Ingress):
#     # Get ALB address:
#     kubectl get ingress -n default -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}'
#     # Set in terraform.tfvars:   alb_dns_name = "<alb-address>"
#     terraform apply -target=module.dns_tls   # creates Route53 alias records
#
# Estimated cost (Phase 10 additions):
#   Route53 hosted zone : $0.50/month (negligible)
#   cert-manager        : no additional cost (runs on existing node)
#   ArgoCD              : no additional cost (runs on existing node)
#   ALB                 : included in nginx ingress controller cost
#   ─────────────────────────────────────────────────────────────
#   EKS + node + NAT    : ~$5.28/day (unchanged from Phase 8)
#
# RULE: Run `terraform destroy` every evening. Never leave EKS overnight.
# NOTE: Delete all PVCs before destroy:
#   kubectl delete pvc --all -n monitoring

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.13"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.30"
    }
  }

  backend "s3" {
    bucket       = "guardops-tfstate-236796665744"
    key          = "guardops/prod/terraform.tfstate"
    region       = "ap-south-1"
    use_lockfile = true
    encrypt      = true
  }
}


# ── AWS provider ───────────────────────────────────────────────────────────────

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

# ── EKS auth data sources ─────────────────────────────────────────────────────

data "aws_eks_cluster" "guardops" {
  name       = "${var.project_name}-${var.environment}-cluster"
  depends_on = [module.eks]
}

data "aws_eks_cluster_auth" "guardops" {
  name       = "${var.project_name}-${var.environment}-cluster"
  depends_on = [module.eks]
}

provider "helm" {
  kubernetes {
    host                   = data.aws_eks_cluster.guardops.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.guardops.certificate_authority[0].data)
    token                  = data.aws_eks_cluster_auth.guardops.token
  }
}

provider "kubernetes" {
  host                   = data.aws_eks_cluster.guardops.endpoint
  cluster_ca_certificate = base64decode(data.aws_eks_cluster.guardops.certificate_authority[0].data)
  token                  = data.aws_eks_cluster_auth.guardops.token
}


# ── Always-on (free tier) ──────────────────────────────────────────────────────

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

# ── Phase 4B+ (costs money) ────────────────────────────────────────────────────

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

# ── Phase 6: GitHub OIDC ───────────────────────────────────────────────────────

module "iam_oidc" {
  source = "./modules/iam_oidc"

  project_name   = var.project_name
  environment    = var.environment
  aws_region     = var.aws_region
  aws_account_id = var.aws_account_id
  github_repo    = var.github_repo
}

output "github_actions_role_arn" {
  description = "Copy this value — set as GitHub secret AWS_ROLE_ARN"
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

# ── Phase 7: Runtime Security ──────────────────────────────────────────────────

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
  value       = var.enable_runtime_security ? module.falco[0].loki_service_url : "Runtime security not enabled — set enable_runtime_security = true"
}

# ── Phase 8: Self-Healing ──────────────────────────────────────────────────────

module "alertmanager_webhook" {
  source = "./modules/alertmanager-webhook"
  count  = var.enable_self_healing ? 1 : 0

  project_name         = var.project_name
  environment          = var.environment
  monitoring_namespace = "monitoring"
  webhook_image        = var.webhook_image

  depends_on = [module.eks, module.falco]
}

output "webhook_service_url" {
  description = "In-cluster webhook URL — paste into k8s/alertmanager/quarantine-webhook.yaml"
  value       = var.enable_self_healing ? module.alertmanager_webhook[0].service_url : "Self-healing not enabled — set enable_self_healing = true"
}

# ── Phase 10: DNS + TLS ────────────────────────────────────────────────────────
#
# Gated by enable_dns_tls (default: false) — same gate pattern as Phase 7/8.
#
# WHY THE GATE:
#   Route53 records reference the ALB DNS name which only exists after the
#   first Helm deploy. Apply this module in two sub-steps (see FIRST APPLY):
#     1. enable_dns_tls=true, alb_dns_name="" → creates hosted zone + cert-manager
#     2. Delegate NS records at registrar, run first Helm deploy, get ALB address
#     3. enable_dns_tls=true, alb_dns_name="<alb>" → creates Route53 alias records
#
# PREREQUISITES before setting enable_dns_tls = true:
#   1. domain_name set in terraform.tfvars
#   2. alb_controller_role_arn: IAM role for the AWS Load Balancer Controller.
#      This should be added to the iam module output — see docs/runbooks/terraform-operations.md.

module "dns_tls" {
  source = "./modules/dns-tls"
  count  = var.enable_dns_tls ? 1 : 0

  project_name            = var.project_name
  aws_region              = var.aws_region
  domain_name             = var.domain_name
  cluster_name            = "${var.project_name}-${var.environment}-cluster"
  alb_controller_role_arn = var.alb_controller_role_arn
  vpc_id                  = module.vpc.vpc_id
  alb_dns_name            = var.alb_dns_name
  alb_hosted_zone_id      = var.alb_hosted_zone_id

  # Enable after Phase 5 observability stack is deployed (kube-prometheus-stack
  # creates the ServiceMonitor CRD that cert-manager metrics require).
  enable_cert_manager_metrics = var.enable_runtime_security

  depends_on = [module.eks]
}

output "name_servers" {
  description = "Route53 NS records — delegate these at your domain registrar to activate the hosted zone."
  value       = var.enable_dns_tls ? module.dns_tls[0].name_servers : ["DNS/TLS not enabled — set enable_dns_tls = true"]
}

output "staging_domain" {
  description = "Staging subdomain. Set as environments.staging.domain in .guardops.yaml."
  value       = var.enable_dns_tls ? module.dns_tls[0].staging_domain : ""
}

# ── Phase 10: ArgoCD GitOps ────────────────────────────────────────────────────
#
# Gated by enable_argocd (default: false).
#
# PREREQUISITES before setting enable_argocd = true:
#   1. enable_dns_tls = true (ArgoCD Ingress needs cert-manager for TLS)
#   2. ClusterIssuers applied: kubectl apply -f k8s/tls/clusterissuer-letsencrypt-prod.yaml
#   3. git_repo_url set in terraform.tfvars
#   4. Route53 NS records delegated + DNS propagated (ArgoCD Ingress cert needs HTTP-01 challenge)

module "argocd" {
  source = "./modules/argocd"
  count  = var.enable_argocd ? 1 : 0

  project_name   = var.project_name
  domain_name    = var.domain_name
  git_repo_url   = var.git_repo_url
  eks_dependency = module.eks

  depends_on = [module.eks, module.dns_tls]
}

output "argocd_server_url" {
  description = "ArgoCD UI URL. Set as argocd.url in .guardops.yaml after DNS propagates."
  value       = var.enable_argocd ? module.argocd[0].argocd_server_url : "ArgoCD not enabled — set enable_argocd = true"
}

output "argocd_initial_password_cmd" {
  description = "Run this command to retrieve the initial ArgoCD admin password."
  value       = var.enable_argocd ? module.argocd[0].initial_admin_password_command : "ArgoCD not enabled"
}

output "argocd_generate_token_cmd" {
  description = "Run this after logging in to generate a CI API token. Add result as GitHub secret ARGOCD_TOKEN."
  value       = var.enable_argocd ? module.argocd[0].generate_api_token_command : "ArgoCD not enabled"
}

# infra/terraform/main.tf
#
# GuardOps — Root Terraform Configuration — Phase 8
#
# Changes from Phase 7:
#   - Added alertmanager_webhook module: FastAPI handler pod + RBAC (gated by enable_self_healing)
#   - Added enable_self_healing variable (default false — same gate pattern as enable_runtime_security)
#   - Added webhook_image variable (ECR image for the handler)
#   - Added webhook_service_url output
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
# ── FIRST APPLY INSTRUCTIONS (Phase 8) ────────────────────────────────────────
#
# Same two-phase approach as Phase 7, with one extra step for self-healing:
#
#   Phase 1 — provision AWS infrastructure only:
#     terraform apply -target=module.vpc -target=module.iam -target=module.iam_oidc \
#                     -target=module.eks -target=module.ecr -target=module.s3
#
#   Phase 2 — install observability + runtime security (EKS must be running):
#     .\scripts\setup-observability.ps1
#     .\scripts\setup-runtime-security.ps1
#     # Then apply Falco + webhook modules:
#     terraform apply   # enable_runtime_security = true, enable_self_healing = true
#
#   Phase 3 — wire Alertmanager (after Phase 2 apply):
#     kubectl apply -f k8s/alertmanager/quarantine-webhook.yaml
#     # Verify: kubectl get prometheusrule guardops-self-healing -n monitoring
#     #         kubectl get alertmanagerconfig guardops-self-healing -n monitoring
#
# Or use the script-only approach (no phase 2/3 Terraform needed for falco):
#   .\scripts\setup-runtime-security.ps1
#   guardops deploy --env prod   # builds + pushes the webhook handler image
#   # Then set enable_self_healing = true and apply only the webhook module:
#   terraform apply -target=module.alertmanager_webhook
#
# Estimated cost when running (unchanged from Phase 7):
#   EKS control plane : $0.10/hr  ($2.40/day)
#   t3.large node     : $0.075/hr ($1.80/day)
#   NAT Gateway       : $0.045/hr ($1.08/day)   — single AZ
#   ECR + S3          : ~$0.00    (free tier)
#   ─────────────────────────────────────────────
#   TOTAL             : ~$5.28/day
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
    # Phase 7+: Helm and Kubernetes providers for Falco/Loki/Promtail and the
    # Phase 8 webhook handler.  Require a live EKS cluster — see FIRST APPLY above.
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

# ── Phase 7+: EKS auth data sources (feed Helm + Kubernetes providers) ────────
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
  description = "Copy this value - set as GitHub secret AWS_ROLE_ARN"
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
  description = "In-cluster Loki URL - set as monitoring.loki_url in .guardops.yaml"
  value       = var.enable_runtime_security ? module.falco[0].loki_service_url : "Runtime security not enabled - set enable_runtime_security = true"
}

# ── Phase 8: Self-Healing (Alertmanager Webhook Handler) ─────────────────────
#
# Deploys the alertmanager_handler.py FastAPI service as a K8s Deployment.
# When a CRITICAL Falco alert fires, Alertmanager POSTs to this service and
# it automatically quarantines the offending pod via NetworkPolicy.
#
# Gated by enable_self_healing (default: false) — same pattern as Phase 7.
#
# PREREQUISITES before setting enable_self_healing = true:
#   1. enable_runtime_security = true (Falco must be running to generate alerts)
#   2. The webhook handler Docker image must be built and pushed to ECR.
#      Set webhook_image in terraform.tfvars to the full ECR image reference.
#      Example:
#        webhook_image = "236796665744.dkr.ecr.ap-south-1.amazonaws.com/guardops-prod:webhook-latest"
#
# AFTER APPLY:
#   1. Get the in-cluster webhook URL:
#        terraform output webhook_service_url
#   2. Confirm it matches the URL in k8s/alertmanager/quarantine-webhook.yaml.
#      (It will — both use the same Service name convention.)
#   3. Apply the Alertmanager wiring:
#        kubectl apply -f k8s/alertmanager/quarantine-webhook.yaml
#   4. Verify the handler is running:
#        kubectl rollout status deployment/guardops-alertmanager-webhook -n monitoring
#        kubectl port-forward svc/guardops-alertmanager-webhook 9095:9095 -n monitoring
#        curl http://localhost:9095/healthz
#   5. Set self_healing.enabled = true and self_healing.webhook_url in .guardops.yaml.
#        (guardops quarantine-status reads the namespace from this file.)
#
# NIGHTLY SHUTDOWN:
#   The Deployment is destroyed with terraform destroy — no extra cleanup needed.
#   Unlike Falco/Loki, the webhook handler has no PVCs.

module "alertmanager_webhook" {
  source = "./modules/alertmanager-webhook"
  count  = var.enable_self_healing ? 1 : 0

  project_name         = var.project_name
  environment          = var.environment
  monitoring_namespace = "monitoring"
  webhook_image        = var.webhook_image

  # The webhook handler runs alongside Falco and Alertmanager, so it must
  # wait for both EKS (for the cluster to exist) and Falco (so the monitoring
  # namespace exists and Alertmanager is already deployed).
  depends_on = [module.eks, module.falco]
}

output "webhook_service_url" {
  description = "In-cluster webhook URL - paste into k8s/alertmanager/quarantine-webhook.yaml receivers[*].webhookConfigs[*].url, then kubectl apply that file."
  value       = var.enable_self_healing ? module.alertmanager_webhook[0].service_url : "Self-healing not enabled - set enable_self_healing = true in terraform.tfvars"
}

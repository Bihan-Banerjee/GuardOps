# infra/terraform/variables.tf
#
# GuardOps — Root Module Input Variables
#
# Phase 10 additions:
#   - enable_dns_tls        (gate for dns-tls module)
#   - enable_argocd         (gate for argocd module)
#   - domain_name           (root domain for Route53 + TLS + ArgoCD Ingress)
#   - git_repo_url          (GitHub repo HTTPS URL for ArgoCD source)
#   - alb_controller_role_arn (IRSA role for AWS Load Balancer Controller)
#   - alb_dns_name          (ALB hostname — set after first Helm deploy)
#   - alb_hosted_zone_id    (region-specific ELB zone ID for Route53 alias)

# ── AWS ───────────────────────────────────────────────────────────────────────

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "ap-south-1"
}

variable "aws_account_id" {
  description = "AWS account ID. Used to restrict the AWS provider to one account."
  type        = string
}

# ── Project ───────────────────────────────────────────────────────────────────

variable "project_name" {
  description = "Short project name used as a prefix for all resource names."
  type        = string
  default     = "guardops"
}

variable "environment" {
  description = "Deployment environment: prod, staging, or local."
  type        = string
  default     = "prod"
}

# ── Networking ────────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "List of AZs to create subnets in. Two AZs minimum for EKS."
  type        = list(string)
  default     = ["ap-south-1a", "ap-south-1b"]
}

# ── ECR ───────────────────────────────────────────────────────────────────────

variable "ecr_image_names" {
  description = "List of ECR repository names to create."
  type        = list(string)
  default     = ["guardops-prod"]
}

# ── EKS ───────────────────────────────────────────────────────────────────────

variable "node_instance_type" {
  description = "EC2 instance type for EKS worker nodes."
  type        = string
  default     = "t3.large"
}

variable "node_min_size" {
  description = "Minimum number of nodes in the EKS managed node group."
  type        = number
  default     = 1
}

variable "node_max_size" {
  description = "Maximum number of nodes in the EKS managed node group."
  type        = number
  default     = 3
}

variable "node_desired_size" {
  description = "Desired number of nodes in the EKS managed node group."
  type        = number
  default     = 1
}

variable "enable_cloudwatch_logs" {
  description = "Enable EKS control plane logging to CloudWatch."
  type        = bool
  default     = false
}

# v1.0.0: when true, the reports bucket exposes ONLY the dashboard/* prefix publicly
# so the SPA can read dashboard/snapshot.json without auth while the cluster is down.
# reports/ and metadata/ stay private. Off by default — see modules/s3.
variable "enable_public_snapshot" {
  description = "Expose only the dashboard/* S3 prefix for the public SPA snapshot fallback."
  type        = bool
  default     = false
}

# ── GitHub OIDC (Phase 6) ─────────────────────────────────────────────────────

variable "github_repo" {
  description = "GitHub repo in owner/name format. Used to scope the OIDC trust policy. Example: Bihan-Banerjee/GuardOps"
  type        = string
}

# ── Runtime Security (Phase 7) ────────────────────────────────────────────────

variable "enable_runtime_security" {
  description = "Deploy Falco + Loki + Promtail via the falco Terraform module. Requires a live EKS cluster."
  type        = bool
  default     = false
}

# ── Self-Healing (Phase 8) ────────────────────────────────────────────────────

variable "enable_self_healing" {
  description = "Deploy the Alertmanager webhook handler for automated pod quarantine. Requires enable_runtime_security = true."
  type        = bool
  default     = false
}

variable "webhook_image" {
  description = "Full ECR image reference for the alertmanager webhook handler."
  type        = string
  default     = ""
}

# ── DNS + TLS (Phase 10) ──────────────────────────────────────────────────────

variable "enable_dns_tls" {
  description = "Deploy the dns-tls module: Route53 hosted zone, cert-manager, and the AWS Load Balancer Controller. Apply in two steps — see FIRST APPLY in main.tf. Requires domain_name and alb_controller_role_arn to be set."
  type        = bool
  default     = false
}

variable "domain_name" {
  description = "Root domain for the Route53 hosted zone and TLS certificates. Example: guardops.live. Must be a domain you own and can delegate NS records for."
  type        = string
  default     = "guardops.live"
}

variable "alb_controller_role_arn" {
  description = "IAM role ARN for the AWS Load Balancer Controller IRSA annotation. Should be an output from the iam module. Example: arn:aws:iam::236796665744:role/guardops-alb-controller"
  type        = string
  default     = ""
}

variable "alb_dns_name" {
  description = "DNS name of the ALB provisioned by the nginx ingress controller. Only available after the first guardops deploy creates an Ingress. Leave empty on first apply. Get the value with: kubectl get ingress -n default -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}' then set here and re-apply to create the Route53 alias records."
  type        = string
  default     = ""
}

variable "alb_hosted_zone_id" {
  description = "ELB hosted zone ID for the ALB alias record. Region-specific, maintained by AWS. ap-south-1: ZP97RAFLXTNZK | us-east-1: Z35SXDOTRQ7X7K | eu-west-1: Z32O12XQLNTSW2. Full list: https://docs.aws.amazon.com/general/latest/gr/elb.html"
  type        = string
  default     = "ZP97RAFLXTNZK"
}

# ── ArgoCD GitOps (Phase 10) ──────────────────────────────────────────────────

variable "enable_argocd" {
  description = "Deploy ArgoCD and the GuardOps AppProject + prod/staging Applications. Requires enable_dns_tls = true and ClusterIssuers applied. See FIRST APPLY Phase 4 instructions in main.tf."
  type        = bool
  default     = false
}

variable "git_repo_url" {
  description = "Full HTTPS GitHub repo URL for ArgoCD source configuration. Example: https://github.com/Bihan-Banerjee/GuardOps"
  type        = string
  default     = "https://github.com/Bihan-Banerjee/GuardOps"
}

# ── Admission Control (Phase 11) ──────────────────────────────────────────────

variable "enable_kyverno" {
  description = "Deploy the kyverno module: Kyverno admission controller + IRSA role for reading cosign signatures from ECR. EKS-only — leave false for local k3d. Policies are applied separately by scripts/setup-admission-control.ps1 (or morning-start.ps1)."
  type        = bool
  default     = false
}

variable "kyverno_policy_action" {
  description = "Default validationFailureAction the Kyverno ClusterPolicies ship with: \"Audit\" (record violations in PolicyReports, block nothing) or \"Enforce\" (reject non-compliant/unsigned pods). Start with Audit, verify reports, then flip to Enforce. Read by morning-start.ps1 / setup-admission-control.ps1 when applying k8s/kyverno/*.yaml."
  type        = string
  default     = "Audit"

  validation {
    condition     = contains(["Audit", "Enforce"], var.kyverno_policy_action)
    error_message = "kyverno_policy_action must be either \"Audit\" or \"Enforce\"."
  }
}

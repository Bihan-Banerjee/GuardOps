# infra/terraform/modules/dns-tls/main.tf
#
# Phase 10: Real domain + TLS via cert-manager
#
# Provisions:
#   1. AWS Load Balancer Controller — required on EKS to provision an ALB
#      from an Ingress resource. Uses IRSA (IAM Role for Service Accounts)
#      so no static credentials are needed.
#   2. cert-manager — Kubernetes controller that requests and renews TLS
#      certificates from Let's Encrypt automatically.
#   3. Route53 hosted zone for the root domain (e.g. guardops.dev)
#   4. Route53 A records (apex, staging, argocd subdomains) as ALB aliases
#
# Apply order:
#   1. Apply this module AFTER EKS is running (depends_on = [module.eks])
#   2. After apply: `terraform output name_servers` → delegate these 4 NS
#      records at your domain registrar (Namecheap, GoDaddy, etc.)
#   3. Apply the ClusterIssuer manifests:
#        kubectl apply -f k8s/tls/clusterissuer-letsencrypt-staging.yaml
#        kubectl apply -f k8s/tls/clusterissuer-letsencrypt-prod.yaml
#   4. The first Helm deploy creates the Ingress → ALB is provisioned → DNS
#      record propagates → cert-manager completes the HTTP-01 ACME challenge
#      → TLS certificate is issued and stored in the tlsSecret.
#
# ALB address bootstrap problem:
#   The Route53 alias records need the ALB DNS name, which only exists after
#   the first Helm/Ingress deploy. On first apply, set alb_dns_name = "" to
#   create the hosted zone and cert-manager without the alias records.
#   After the first guardops deploy, get the ALB address:
#     kubectl get ingress -n default -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}'
#   Set alb_dns_name in terraform.tfvars and re-apply to create the alias records.
#
# Estimated cost addition (Phase 10 additions only):
#   Route53 hosted zone : $0.50/month
#   Route53 queries     : $0.40/million (negligible at dev scale)
#   ─────────────────────────────────────────────────────────────
#   TOTAL ADDITION      : ~$0.50/month  (minimal)

# ── AWS Load Balancer Controller ──────────────────────────────────────────────
#
# Required on EKS to provision ALBs from Ingress resources with
# ingressClassName: alb. Also used by the nginx ingress controller
# to get an external LoadBalancer address.
#
# IRSA: the controller authenticates to AWS via its ServiceAccount annotation
# rather than node IAM role — least-privilege best practice.

resource "helm_release" "aws_load_balancer_controller" {
  name             = "aws-load-balancer-controller"
  repository       = "https://aws.github.io/eks-charts"
  chart            = "aws-load-balancer-controller"
  version          = "1.7.2"
  namespace        = "kube-system"

  set {
    name  = "clusterName"
    value = var.cluster_name
  }

  # IRSA annotation — the controller pod assumes this role via the K8s
  # service account token, not node instance profile credentials.
  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = var.alb_controller_role_arn
  }

  set {
    name  = "region"
    value = var.aws_region
  }

  set {
    name  = "vpcId"
    value = var.vpc_id
  }

  # Image hosted on ECR Public — no auth required, no rate limits
  set {
    name  = "image.repository"
    value = "public.ecr.aws/eks/aws-load-balancer-controller"
  }

  timeout = 300
  atomic  = true
  wait    = true
}

# ── cert-manager ──────────────────────────────────────────────────────────────
#
# installCRDs = true: installs the Certificate, ClusterIssuer, CertificateRequest
# CRDs alongside the controller. Without this, `kubectl apply -f clusterissuer-*.yaml`
# would fail with "no kind ClusterIssuer is registered".
#
# After this deploys, apply the ClusterIssuer manifests in k8s/tls/.
# cert-manager then automatically provisions and renews certificates whenever
# an Ingress with the cert-manager.io/cluster-issuer annotation is created.

resource "helm_release" "cert_manager" {
  name             = "cert-manager"
  repository       = "https://charts.jetstack.io"
  chart            = "cert-manager"
  version          = "v1.14.4"
  namespace        = "cert-manager"
  create_namespace = true

  set {
    name  = "installCRDs"
    value = "true"
  }

  set {
    name  = "prometheus.enabled"
    value = tostring(var.enable_cert_manager_metrics)
  }

  # Recommended for EKS: use the leader election namespace to avoid
  # cert-manager accidentally watching cluster-wide resources it doesn't own.
  set {
    name  = "global.leaderElection.namespace"
    value = "cert-manager"
  }

  timeout = 300
  atomic  = true
  wait    = true

  # cert-manager needs the cluster to exist but does NOT need the ALB
  # controller to be running first — they can coexist in any order.
}

# ── Route53 hosted zone ───────────────────────────────────────────────────────
#
# Creates the hosted zone for the root domain. After apply:
#   terraform output name_servers
# Copy the four NS values to your domain registrar's DNS settings.
# DNS propagation takes 24-48h but is usually complete within 30 min.

resource "aws_route53_zone" "guardops" {
  name = var.domain_name

  tags = {
    Project   = var.project_name
    ManagedBy = "terraform"
    Phase     = "10"
  }
}

# ── Route53 alias records ─────────────────────────────────────────────────────
#
# Alias records are only created when alb_dns_name is non-empty
# (i.e. after the first Helm deploy provisions the ALB).
# On first apply, skip these by leaving alb_dns_name = "" in terraform.tfvars.
#
# Using `count` keeps the plan clean — Terraform simply shows 0 resources
# to create when ALB is not yet provisioned rather than creating records
# that point at nothing.

locals {
  alb_ready = var.alb_dns_name != ""
}

resource "aws_route53_record" "prod_apex" {
  count = local.alb_ready ? 1 : 0

  zone_id = aws_route53_zone.guardops.zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_hosted_zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "staging" {
  count = local.alb_ready ? 1 : 0

  zone_id = aws_route53_zone.guardops.zone_id
  name    = "staging.${var.domain_name}"
  type    = "A"

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_hosted_zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "argocd" {
  count = local.alb_ready ? 1 : 0

  zone_id = aws_route53_zone.guardops.zone_id
  name    = "argocd.${var.domain_name}"
  type    = "A"

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_hosted_zone_id
    evaluate_target_health = true
  }
}

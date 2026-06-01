# infra/terraform/modules/kyverno/main.tf
#
# GuardOps Phase 11 — Admission Control (Kyverno)
#
# Installs Kyverno (policy engine + admission webhook) and the IRSA role that
# lets its admission and background controllers READ cosign signatures and
# attestations from the PRIVATE ECR repository during image verification.
#
# Why IRSA (not just the node role):
#   Kyverno's verifyImages rules pull the cosign .sig / .att OCI artifacts from
#   ECR using the in-pod registry client. IRSA gives those two controllers a
#   least-privilege, ServiceAccount-scoped role instead of relying on the shared
#   node instance profile. The node role already carries ECR ReadOnly, so even
#   if IRSA were misconfigured the pods fall back to node credentials via IMDS —
#   but IRSA is the correct, auditable path and the one we wire here.
#
# The ClusterPolicies themselves are NOT applied by this module. They live as
# files in k8s/kyverno/ and are applied with `kubectl apply` AFTER Kyverno is
# Ready (scripts/setup-admission-control.ps1 / morning-start.ps1) — the same
# pattern the repo uses for the ArgoCD Applications and cert-manager
# ClusterIssuers. This keeps Terraform from needing the Kyverno CRD schema at
# plan time (before the CRDs exist).
#
# The helm provider is configured in the root main.tf from the EKS outputs.

terraform {
  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.13"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  # Default Kyverno chart v3 ServiceAccount names (release name "kyverno").
  # These are the two controllers that perform image verification and therefore
  # need to reach ECR.
  sa_admission  = "kyverno-admission-controller"
  sa_background = "kyverno-background-controller"

  # IRSA trust conditions key on the issuer host without the https:// scheme.
  oidc_host = replace(var.oidc_provider_url, "https://", "")
}

# ── IRSA role: ECR read for Kyverno image verification ────────────────────────

# nosemgrep: no-iam-resource-exposure
resource "aws_iam_role" "kyverno_ecr" {
  name = "${var.project_name}-kyverno-ecr-read"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = var.oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_host}:aud" = "sts.amazonaws.com"
          "${local.oidc_host}:sub" = [
            "system:serviceaccount:${var.kyverno_namespace}:${local.sa_admission}",
            "system:serviceaccount:${var.kyverno_namespace}:${local.sa_background}",
          ]
        }
      }
    }]
  })

  tags = {
    Name    = "${var.project_name}-kyverno-ecr-read"
    Phase   = "11"
    Purpose = "kyverno-verify-images-ecr-read"
  }
}

# nosemgrep: no-iam-resource-exposure
resource "aws_iam_role_policy" "kyverno_ecr" {
  name = "${var.project_name}-kyverno-ecr-read"
  role = aws_iam_role.kyverno_ecr.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # ECR auth token is account-wide and cannot be resource-scoped.
        Sid      = "ECRAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        # Read-only image + layer access, scoped to GuardOps repositories so a
        # leaked Kyverno token cannot read unrelated repos.
        Sid    = "ECRReadGuardOps"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:DescribeImages",
          "ecr:ListImages",
        ]
        Resource = [
          "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/${var.project_name}*",
          "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/guardops*",
        ]
      },
    ]
  })
}

# ── Kyverno Helm release ──────────────────────────────────────────────────────
#
# Chart 3.4.x ships Kyverno 1.14.x. (Pinned to 3.4.6: chart 3.2.x pulled its
# report-cleanup jobs/hooks from bitnami/kubectl:1.28.5, which was deleted from
# Docker Hub — ImagePullBackOff blocked the wait=true install AND the uninstall
# hooks. 3.4.x uses reg.kyverno.io / alpine/kubectl, fixing it at the source.)
# Installing Kyverno with no policies is inert: the webhooks only act on resources
# matched by the ClusterPolicies applied later from k8s/kyverno/. That separation
# is what makes the Audit → Enforce flip safe.

resource "helm_release" "kyverno" {
  name             = "kyverno"
  repository       = "https://kyverno.github.io/kyverno/"
  chart            = "kyverno"
  version          = "3.4.6"
  namespace        = var.kyverno_namespace
  create_namespace = true

  timeout = 600
  atomic  = true
  wait    = true

  # ── IRSA annotations on the two image-verifying controllers ─────────────────
  # The escaped dots stop Helm from splitting the annotation key into nested
  # YAML. The EKS Pod Identity webhook reads this annotation and injects the
  # AWS_ROLE_ARN / web-identity token the AWS SDK picks up automatically.
  set {
    name  = "admissionController.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.kyverno_ecr.arn
  }

  set {
    name  = "backgroundController.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.kyverno_ecr.arn
  }

  # ── Single-node sizing (cost-conscious cluster) ─────────────────────────────
  # One admission replica — the cluster is a single t3.large shared with the
  # observability stack. Raise to 3 for HA on a multi-node production cluster.
  set {
    name  = "admissionController.replicas"
    value = "1"
  }

  set {
    name  = "admissionController.container.resources.requests.cpu"
    value = "100m"
  }

  set {
    name  = "admissionController.container.resources.requests.memory"
    value = "128Mi"
  }

  set {
    name  = "admissionController.container.resources.limits.memory"
    value = "384Mi"
  }

  # ── Lean install on a single, busy node (Phase 11) ──────────────────────────
  # Disable the policyReportsCleanup post-install hook: it is a blocking helm hook
  # and only prunes old policy reports — pointless on a cluster destroyed nightly,
  # and one less thing for wait=true to block on. (Chart 3.4.x's cleanup images
  # come from reg.kyverno.io/alpine, so this is no longer about the bitnami purge.)
  set {
    name  = "policyReportsCleanup.enabled"
    value = "false"
  }

  depends_on = [
    var.eks_dependency,
    aws_iam_role_policy.kyverno_ecr,
  ]
}

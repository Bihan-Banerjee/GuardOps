# infra/terraform/modules/kyverno/variables.tf
#
# GuardOps Phase 11 — Admission Control (Kyverno)

variable "project_name" {
  description = "Short project name used as a prefix for IAM resource names."
  type        = string
}

variable "aws_region" {
  description = "AWS region of the ECR repositories Kyverno reads signatures from."
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID. Used to scope the ECR read policy resource ARNs."
  type        = string
}

variable "oidc_provider_arn" {
  description = "ARN of the EKS cluster IRSA OIDC provider (module.eks.oidc_provider_arn)."
  type        = string
}

variable "oidc_provider_url" {
  description = "Issuer URL of the EKS cluster OIDC provider (module.eks.oidc_provider_url). The scheme is stripped for the trust-policy condition keys."
  type        = string
}

variable "kyverno_namespace" {
  description = "Namespace Kyverno is installed into. The IRSA trust policy is scoped to the admission/background controller ServiceAccounts in this namespace."
  type        = string
  default     = "kyverno"
}

variable "policy_action" {
  description = "Default validationFailureAction the ClusterPolicies ship with: Audit (record only) or Enforce (block). Echoed as an output for the setup scripts; the policy manifests themselves are applied by scripts/setup-admission-control.ps1, not this module."
  type        = string
  default     = "Audit"

  validation {
    condition     = contains(["Audit", "Enforce"], var.policy_action)
    error_message = "policy_action must be either \"Audit\" or \"Enforce\"."
  }
}

variable "eks_dependency" {
  description = "Dependency token from the EKS module to enforce apply ordering. Pass module.eks so EKS exists before Kyverno's Helm provider tries to connect."
  type        = any
  default     = null
}

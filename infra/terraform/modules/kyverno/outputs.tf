# infra/terraform/modules/kyverno/outputs.tf

output "kyverno_release_status" {
  description = "Helm release status for Kyverno (deployed / failed)."
  value       = helm_release.kyverno.status
}

output "kyverno_namespace" {
  description = "Namespace Kyverno is installed into."
  value       = var.kyverno_namespace
}

output "kyverno_ecr_role_arn" {
  description = "ARN of the IRSA role the Kyverno admission/background controllers assume to read cosign signatures from ECR."
  value       = aws_iam_role.kyverno_ecr.arn
}

output "policy_action" {
  description = "Default validationFailureAction the ClusterPolicies are applied with (Audit or Enforce)."
  value       = var.policy_action
}

# infra/terraform/modules/iam/outputs.tf

output "eks_cluster_role_arn" {
  description = "ARN of the EKS cluster role. Passed to the EKS module."
  value       = aws_iam_role.eks_cluster.arn
}

output "eks_node_role_arn" {
  description = "ARN of the EKS node role. Passed to the EKS module."
  value       = aws_iam_role.eks_node.arn
}

# ci_user_* outputs removed in Phase 6/11 — the static CI user was replaced by
# GitHub OIDC (modules/iam_oidc). CI auth no longer uses AWS_ACCESS_KEY_ID/SECRET.

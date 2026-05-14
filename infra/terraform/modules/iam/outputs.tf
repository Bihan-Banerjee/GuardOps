# infra/terraform/modules/iam/outputs.tf

output "eks_cluster_role_arn" {
  description = "ARN of the EKS cluster role. Passed to the EKS module."
  value       = aws_iam_role.eks_cluster.arn
}

output "eks_node_role_arn" {
  description = "ARN of the EKS node role. Passed to the EKS module."
  value       = aws_iam_role.eks_node.arn
}

output "ci_user_name" {
  description = "IAM username for GitHub Actions."
  value       = aws_iam_user.ci.name
}

output "ci_user_access_key_id" {
  description = "Access key ID for CI user. Set as AWS_ACCESS_KEY_ID in GitHub Secrets."
  value       = aws_iam_access_key.ci.id
}

output "ci_user_secret_access_key" {
  description = "Secret access key for CI user. Set as AWS_SECRET_ACCESS_KEY in GitHub Secrets."
  value       = aws_iam_access_key.ci.secret
  sensitive   = true  # won't print in logs; use: terraform output -raw ci_user_secret_access_key
}

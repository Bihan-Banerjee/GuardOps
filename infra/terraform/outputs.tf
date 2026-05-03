# infra/terraform/outputs.tf
# These values are printed after terraform apply and are used in CI/CD.

output "eks_cluster_name" {
  description = "EKS cluster name — pass to aws eks update-kubeconfig"
  value       = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  description = "EKS API server endpoint"
  value       = module.eks.cluster_endpoint
}

output "ecr_repository_url" {
  description = "ECR repository URL — set as ECR_REGISTRY in GitHub Secrets"
  value       = module.ecr.repository_url
}

output "s3_reports_bucket" {
  description = "S3 bucket for security reports — set as GUARDOPS_S3_BUCKET"
  value       = module.s3.bucket_name
}

output "ci_user_access_key_id" {
  description = "IAM access key for CI/CD — set as AWS_ACCESS_KEY_ID in GitHub Secrets"
  value       = module.iam.ci_user_access_key_id
  sensitive   = true
}

output "ci_user_secret_access_key" {
  description = "IAM secret key for CI/CD — set as AWS_SECRET_ACCESS_KEY in GitHub Secrets"
  value       = module.iam.ci_user_secret_access_key
  sensitive   = true
}

output "kubeconfig_command" {
  description = "Run this command to configure kubectl to talk to your EKS cluster"
  value = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

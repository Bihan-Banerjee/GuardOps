# infra/terraform/outputs.tf — Phase 4B

output "ecr_registry_url" {
  value = module.ecr.registry_url
}

output "ecr_repository_urls" {
  value = module.ecr.repository_urls
}

output "s3_reports_bucket_name" {
  value = module.s3.reports_bucket_name
}

# ── Phase 4B outputs ──────────────────────────────────────────────────────────

output "eks_cluster_name" {
  description = "Run: aws eks update-kubeconfig --region ap-south-1 --name <this value>"
  value       = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "ci_user_access_key_id" {
  description = "Update AWS_ACCESS_KEY_ID GitHub Secret with this value."
  value       = module.iam.ci_user_access_key_id
}

output "ci_user_secret_access_key" {
  description = "Run: terraform output -raw ci_user_secret_access_key"
  value       = module.iam.ci_user_secret_access_key
  sensitive   = true
}

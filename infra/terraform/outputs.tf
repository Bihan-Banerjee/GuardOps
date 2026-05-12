# infra/terraform/outputs.tf
#
# Outputs printed after `terraform apply`.
# Copy these values into your GitHub Secrets and .guardops.yaml.

output "ecr_registry_url" {
  description = "ECR registry base URL. Set this as your docker.registry in .guardops.yaml for prod."
  value       = module.ecr.registry_url
}

output "ecr_repository_urls" {
  description = "Full URL for each ECR repository. Use guardops-app URL when pushing images."
  value       = module.ecr.repository_urls
}

output "s3_reports_bucket_name" {
  description = "S3 bucket name for scan reports. Set as GUARDOPS_S3_BUCKET in GitHub Secrets."
  value       = module.s3.reports_bucket_name
}

output "s3_reports_bucket_arn" {
  description = "S3 bucket ARN — needed if you later want to create IAM policies for it."
  value       = module.s3.reports_bucket_arn
}

# PHASE 4B outputs — uncomment when VPC/IAM/EKS modules are active
#
# output "vpc_id" {
#   description = "VPC ID for the EKS cluster."
#   value       = module.vpc.vpc_id
# }
#
# output "eks_cluster_endpoint" {
#   description = "EKS cluster API endpoint. Used by kubectl and guardops deploy --env prod."
#   value       = module.eks.cluster_endpoint
# }
#
# output "eks_cluster_name" {
#   description = "EKS cluster name. Run: aws eks update-kubeconfig --name <this value>"
#   value       = module.eks.cluster_name
# }

# infra/terraform/outputs.tf
#
# GuardOps - Root Module Outputs
# Note: github_actions_role_arn, falco_loki_url, webhook_service_url are defined in main.tf.

output "eks_cluster_name" {
  description = "EKS cluster name - used by morning-start.ps1 to run aws eks update-kubeconfig."
  value       = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = module.eks.cluster_endpoint
}

output "ecr_registry_url" {
  description = "ECR registry base URL - set as docker.registry in .guardops.yaml."
  value       = module.ecr.registry_url
}

output "ecr_repository_urls" {
  description = "Map of image name to full ECR repository URL."
  value       = module.ecr.repository_urls
}

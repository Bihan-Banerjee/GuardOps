# infra/terraform/modules/eks/outputs.tf

output "cluster_name" {
  description = "EKS cluster name. Use in: aws eks update-kubeconfig --name <this>"
  value       = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint. Used by kubectl and Helm."
  value       = aws_eks_cluster.main.endpoint
}

output "cluster_ca_certificate" {
  description = "Base64-encoded cluster CA certificate."
  value       = aws_eks_cluster.main.certificate_authority[0].data
  sensitive   = true
}

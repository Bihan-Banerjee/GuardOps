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

# ── IRSA (Phase 11) ───────────────────────────────────────────────────────────

output "oidc_provider_arn" {
  description = "ARN of the cluster IRSA OIDC identity provider. Pass to modules that need IRSA roles (e.g. kyverno)."
  value       = aws_iam_openid_connect_provider.eks.arn
}

output "oidc_provider_url" {
  description = "Issuer URL of the cluster OIDC provider (includes https://). IRSA trust conditions strip the scheme."
  value       = aws_iam_openid_connect_provider.eks.url
}

# infra/terraform/modules/ecr/outputs.tf

output "registry_url" {
  description = "ECR registry base URL (account + region, no repo name). Use as docker.registry in .guardops.yaml."
  # All repos share the same registry base — just the repo name differs
  value = length(var.image_names) > 0 ? (
    replace(
      aws_ecr_repository.repos[var.image_names[0]].repository_url,
      "/${var.image_names[0]}",
      ""
    )
  ) : ""
}

output "repository_urls" {
  description = "Map of image name → full ECR repository URL."
  value = {
    for name, repo in aws_ecr_repository.repos : name => repo.repository_url
  }
}

output "repository_arns" {
  description = "Map of image name → repository ARN. Needed for IAM policies in Phase 4B."
  value = {
    for name, repo in aws_ecr_repository.repos : name => repo.arn
  }
}

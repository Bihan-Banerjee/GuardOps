# infra/terraform/modules/iam_oidc/outputs.tf

output "github_actions_role_arn" {
  description = <<-EOT
    ARN of the IAM role GitHub Actions assumes via OIDC.
    Add this as a GitHub secret named AWS_ROLE_ARN:
      gh secret set AWS_ROLE_ARN --body "$(terraform output -raw github_actions_role_arn)"
  EOT
  value       = aws_iam_role.github_actions.arn
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub OIDC identity provider registered with AWS"
  value       = aws_iam_openid_connect_provider.github.arn
}

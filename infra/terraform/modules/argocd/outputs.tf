# infra/terraform/modules/argocd/outputs.tf

output "argocd_server_url" {
  description = (
    "ArgoCD UI URL. Set this as argocd.url in .guardops.yaml after DNS propagates: "
    "  argocd:"
    "    url: \"https://argocd.<domain>\""
  )
  value = "https://argocd.${var.domain_name}"
}

output "argocd_namespace" {
  description = "Namespace where ArgoCD is installed."
  value       = helm_release.argocd.namespace
}

output "prod_app_name" {
  description = "ArgoCD Application name for prod. Set as argocd.app_name_prod in .guardops.yaml."
  value       = "guardops-app-prod"
}

output "staging_app_name" {
  description = "ArgoCD Application name for staging. Set as argocd.app_name_staging in .guardops.yaml."
  value       = "guardops-app-staging"
}

output "initial_admin_password_command" {
  description = "Command to retrieve the initial ArgoCD admin password after first apply."
  value       = "kubectl get secret argocd-initial-admin-secret -n argocd -o jsonpath='{.data.password}' | base64 -d"
}

output "generate_api_token_command" {
  description = "Command to generate an ArgoCD API token for CI (run after logging in with the admin password)."
  value       = "argocd account generate-token --account admin"
}

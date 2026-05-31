# infra/terraform/modules/argocd/variables.tf

variable "project_name" {
  description = "Short project name used as a label value on ArgoCD resources."
  type        = string
}

variable "domain_name" {
  description = "Root domain for the ArgoCD Ingress. ArgoCD UI is served at argocd.<domain_name>. Must match the domain managed by the dns-tls module."
  type        = string
}

variable "git_repo_url" {
  description = "Full HTTPS GitHub repo URL. Used as the ArgoCD source repo and in the AppProject sourceRepos allowlist. Example: https://github.com/Bihan-Banerjee/GuardOps"
  type        = string
}

variable "eks_dependency" {
  description = "Dependency token from the EKS module to enforce apply ordering. Pass module.eks (or any output from it) to ensure EKS exists before ArgoCD's Helm provider tries to connect."
  type        = any
  default     = null
}

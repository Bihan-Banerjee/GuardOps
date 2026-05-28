# infra/terraform/modules/dns-tls/outputs.tf

output "route53_zone_id" {
  description = "Route53 hosted zone ID. Use when creating additional DNS records outside this module."
  value       = aws_route53_zone.guardops.zone_id
}

output "name_servers" {
  description = (
    "The four Route53 NS records for the hosted zone. "
    "Copy these to your domain registrar's DNS settings to delegate the zone. "
    "DNS propagation typically completes within 30 min but can take up to 48h."
  )
  value = aws_route53_zone.guardops.name_servers
}

output "domain_name" {
  description = "Root domain managed by this module."
  value       = var.domain_name
}

output "staging_domain" {
  description = "Staging subdomain. Set as environments.staging.domain in .guardops.yaml."
  value       = "staging.${var.domain_name}"
}

output "argocd_domain" {
  description = "ArgoCD UI subdomain. Set as argocd.url in .guardops.yaml after DNS propagates."
  value       = "argocd.${var.domain_name}"
}

output "cert_manager_namespace" {
  description = "Namespace where cert-manager is installed."
  value       = helm_release.cert_manager.namespace
}

output "alb_controller_release" {
  description = "Helm release name of the AWS Load Balancer Controller."
  value       = helm_release.aws_load_balancer_controller.name
}

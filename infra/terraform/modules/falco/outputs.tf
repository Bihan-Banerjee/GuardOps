# infra/terraform/modules/falco/outputs.tf

output "falco_release_name" {
  description = "Helm release name for Falco"
  value       = helm_release.falco.name
}

output "falco_release_status" {
  description = "Helm release status for Falco (deployed, pending, failed)"
  value       = helm_release.falco.status
}

output "loki_release_name" {
  description = "Helm release name for Loki"
  value       = helm_release.loki.name
}

output "promtail_release_name" {
  description = "Helm release name for Promtail"
  value       = helm_release.promtail.name
}

output "loki_service_url" {
  description = "In-cluster URL to reach Loki from Grafana or guardops runtime-status"
  value       = "http://loki.${var.monitoring_namespace}.svc.cluster.local:3100"
}

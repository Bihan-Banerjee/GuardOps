# infra/terraform/modules/alertmanager-webhook/outputs.tf

output "service_url" {
  description = "In-cluster ClusterIP URL for the webhook handler. Paste into k8s/alertmanager/quarantine-webhook.yaml receivers[*].webhookConfigs[*].url."
  value       = "http://${kubernetes_service.webhook.metadata[0].name}.${kubernetes_service.webhook.metadata[0].namespace}.svc.cluster.local:${var.webhook_port}/webhook"
}

output "healthz_url" {
  description = "In-cluster liveness probe URL. Verify with: kubectl exec -n monitoring <any-pod> -- curl <this-url>"
  value       = "http://${kubernetes_service.webhook.metadata[0].name}.${kubernetes_service.webhook.metadata[0].namespace}.svc.cluster.local:${var.webhook_port}/healthz"
}

output "service_name" {
  description = "Kubernetes Service name. Port-forward with: kubectl port-forward svc/<name> 9095:9095 -n monitoring"
  value       = kubernetes_service.webhook.metadata[0].name
}

output "service_account_name" {
  description = "ServiceAccount used by the handler pod."
  value       = kubernetes_service_account.webhook.metadata[0].name
}

output "deployment_name" {
  description = "Kubernetes Deployment name. Check rollout with: kubectl rollout status deployment/<name> -n monitoring"
  value       = kubernetes_deployment.webhook.metadata[0].name
}

output "namespace" {
  description = "Kubernetes namespace where all module resources are deployed."
  value       = var.monitoring_namespace
}

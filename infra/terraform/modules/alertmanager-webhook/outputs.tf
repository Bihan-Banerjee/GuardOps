# infra/terraform/modules/alertmanager-webhook/outputs.tf
#
# GuardOps — Phase 8 — Alertmanager Webhook Handler Module — Outputs
#
# Outputs follow the same conventions as other GuardOps modules:
#   - Every output has a description explaining exactly how to use the value.
#   - Sensitive values (none here — service URLs are not secret) don't use
#     sensitive = true so they display in `terraform output` without masking.
#
# ROOT MODULE USAGE:
#   In infra/terraform/main.tf, wire this module and expose the webhook URL:
#
#     module "alertmanager_webhook" {
#       source               = "./modules/alertmanager-webhook"
#       count                = var.enable_self_healing ? 1 : 0
#       project_name         = var.project_name
#       environment          = var.environment
#       monitoring_namespace = "monitoring"
#       webhook_image        = "<ecr_url>/<image>:<tag>"
#       depends_on           = [module.eks, module.falco]
#     }
#
#     output "webhook_service_url" {
#       value = var.enable_self_healing ? module.alertmanager_webhook[0].service_url : "self-healing not enabled"
#     }

output "service_url" {
  description = (
    "In-cluster ClusterIP URL for the webhook handler. "
    "Use this as the webhook URL in the Alertmanager receiver config: "
    "k8s/alertmanager/quarantine-webhook.yaml → receivers[*].webhook_configs[*].url. "
    "Example: http://guardops-alertmanager-webhook.monitoring.svc.cluster.local:9095/webhook"
  )
  value = (
    "http://${kubernetes_service.webhook.metadata[0].name}"
    + ".${kubernetes_service.webhook.metadata[0].namespace}"
    + ".svc.cluster.local"
    + ":${var.webhook_port}/webhook"
  )
}

output "healthz_url" {
  description = (
    "In-cluster liveness probe URL. "
    "Useful for manual verification that the handler is running: "
    "  kubectl exec -n monitoring <any-pod> -- curl <this-url>"
  )
  value = (
    "http://${kubernetes_service.webhook.metadata[0].name}"
    + ".${kubernetes_service.webhook.metadata[0].namespace}"
    + ".svc.cluster.local"
    + ":${var.webhook_port}/healthz"
  )
}

output "service_name" {
  description = (
    "Kubernetes Service name. "
    "Use with kubectl to port-forward for local testing: "
    "  kubectl port-forward svc/<name> 9095:9095 -n monitoring"
  )
  value = kubernetes_service.webhook.metadata[0].name
}

output "service_account_name" {
  description = (
    "Name of the ServiceAccount used by the handler pod. "
    "The ClusterRole bound to this account grants the RBAC permissions "
    "needed to label pods, manage NetworkPolicies, and drain nodes."
  )
  value = kubernetes_service_account.webhook.metadata[0].name
}

output "deployment_name" {
  description = (
    "Kubernetes Deployment name. "
    "Use to inspect rollout status: "
    "  kubectl rollout status deployment/<name> -n monitoring"
  )
  value = kubernetes_deployment.webhook.metadata[0].name
}

output "namespace" {
  description = "Kubernetes namespace where all module resources are deployed."
  value       = var.monitoring_namespace
}

# infra/terraform/modules/argocd/main.tf
#
# Phase 10 / v1.0.0: ArgoCD GitOps controller (Helm release only).
#
# This module installs the ArgoCD Helm release. The AppProject and the prod/staging
# Applications are applied from k8s/argocd/*.yaml by scripts/morning-start.ps1.
#
# WHY apps are not kubernetes_manifest resources here (v1.0.0 bug fix):
#   `kubernetes_manifest` validates against the cluster API at PLAN time, but the
#   argoproj.io CRDs don't exist until this Helm release installs them in the same
#   apply — so planning failed with "no matches for kind AppProject". Applying the
#   identical manifests in k8s/argocd/*.yaml via kubectl (after the CRDs exist)
#   avoids the bootstrap ordering problem entirely and keeps a single source of
#   truth for the Application specs.
#
# WHY the ingress is not managed by the chart (v1.0.0 bug fix):
#   The chart's ingress defaulted to ingressClassName=nginx, but the cluster runs
#   the AWS Load Balancer Controller (ALB), not nginx — so the UI was unreachable.
#   k8s/argocd/ingress.yaml exposes argocd.<domain> on the shared "guardops" ALB
#   group (same proven pattern as k8s/dashboard/ingress.yaml), applied by kubectl.
#
# Prerequisites before applying this module:
#   1. EKS cluster running (depends_on = [var.eks_dependency])
#   2. dns-tls module applied (Route53 + ALB controller) so the ingress gets an ALB
#
# After apply:
#   1. kubectl apply -f k8s/argocd/   (AppProject, Applications, Ingress)
#   2. Get the initial admin password:
#        kubectl get secret argocd-initial-admin-secret -n argocd \
#          -o jsonpath='{.data.password}' | base64 -d
#   3. Create an API token for CI:  argocd account generate-token --account admin
#   4. Set argocd.url in .guardops.yaml to https://argocd.<domain>

resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = "6.7.3"
  namespace        = "argocd"
  create_namespace = true

  # Run ArgoCD server in insecure mode — TLS is terminated at the ALB, which then
  # forwards plain HTTP to the server on port 80 (k8s/argocd/ingress.yaml).
  set {
    name  = "server.insecure"
    value = "true"
  }

  # The chart's ingress is disabled — see the header note. argocd.<domain> is served
  # by the static ALB Ingress in k8s/argocd/ingress.yaml instead.
  set {
    name  = "server.ingress.enabled"
    value = "false"
  }

  # ── Metrics ─────────────────────────────────────────────────────────────────
  # ArgoCD exposes Prometheus metrics for the application controller, repo server,
  # and server components. Grafana dashboard ID: 14584.
  set {
    name  = "controller.metrics.enabled"
    value = "true"
  }

  set {
    name  = "server.metrics.enabled"
    value = "true"
  }

  set {
    name  = "repoServer.metrics.enabled"
    value = "true"
  }

  timeout = 600 # ArgoCD takes longer than most charts to become ready
  atomic  = true
  wait    = true

  depends_on = [var.eks_dependency]
}

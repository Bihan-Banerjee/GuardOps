# infra/terraform/modules/argocd/main.tf
#
# Phase 10: ArgoCD GitOps controller + GuardOps Applications
#
# Provisions:
#   1. ArgoCD Helm release in the "argocd" namespace
#   2. GuardOps AppProject — least-privilege scope for what ArgoCD can deploy
#   3. ArgoCD Application for prod (auto-sync DISABLED — CI triggers explicitly)
#   4. ArgoCD Application for staging (auto-sync ENABLED — commits auto-deploy)
#
# Design decisions:
#
#   Both Applications point at the same Helm chart (k8s/helm/guardops-app/).
#   Each loads its own overlay stack:
#     values.yaml → values-<env>.yaml → values-override-<env>.yaml
#   values-override-<env>.yaml is the ONLY file guardops deploy modifies.
#   It contains just image.repository and image.tag. All other values
#   (resources, probes, HPA, ingress, TLS) live in values-<env>.yaml
#   and are only changed by deliberate operator commits.
#
#   Prod auto-sync is DISABLED intentionally:
#     - Every prod deploy requires a human step (CI sync-gate or manual argocd sync)
#     - This gives operators a clear "point of control" between image promotion
#       and prod reconciliation, even in a fully automated pipeline
#     - The CI sync-gate job (`guardops sync-status --env prod --wait`) provides
#       the automation without removing the explicit trigger requirement
#
#   Staging auto-sync is ENABLED:
#     - Staging is the fast feedback loop — every commit to main that touches
#       values-override-staging.yaml is applied within ~3 minutes
#     - selfHeal=true means ArgoCD reverts any manual kubectl changes in staging,
#       keeping it reliably reproducible
#
# Prerequisites before applying this module:
#   1. EKS cluster running (depends_on = [var.eks_dependency])
#   2. Route53 + cert-manager applied (dns-tls module) so the argocd Ingress
#      can get a TLS certificate from Let's Encrypt
#   3. ClusterIssuers applied: kubectl apply -f k8s/tls/clusterissuer-letsencrypt-prod.yaml
#
# After apply:
#   1. Get the initial admin password:
#        kubectl get secret argocd-initial-admin-secret -n argocd \
#          -o jsonpath='{.data.password}' | base64 -d
#   2. Log in to the UI at https://argocd.<domain>
#   3. Create an API token for CI:
#        argocd account generate-token --account admin
#        gh secret set ARGOCD_TOKEN --body "<token>"
#   4. Set argocd.url in .guardops.yaml:
#        argocd:
#          url: "https://argocd.<domain>"

# ── ArgoCD Helm release ───────────────────────────────────────────────────────

resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = "6.7.3"
  namespace        = "argocd"
  create_namespace = true

  # Run ArgoCD server in insecure mode — TLS is terminated at the ALB/Ingress.
  # This is the recommended pattern when using an ingress controller with TLS.
  set {
    name  = "server.insecure"
    value = "true"
  }

  # ── ArgoCD UI Ingress ───────────────────────────────────────────────────────
  # Exposes the ArgoCD server at argocd.<domain> with Let's Encrypt TLS.
  # cert-manager picks up the cluster-issuer annotation and provisions the cert.

  set {
    name  = "server.ingress.enabled"
    value = "true"
  }

  set {
    name  = "server.ingress.ingressClassName"
    value = "nginx"
  }

  set {
    name  = "server.ingress.hostname"
    value = "argocd.${var.domain_name}"
  }

  set {
    name  = "server.ingress.tls"
    value = "true"
  }

  # cert-manager annotation — triggers automatic certificate provisioning
  set {
    name  = "server.ingress.annotations.cert-manager\\.io/cluster-issuer"
    value = "letsencrypt-prod"
  }

  set {
    name  = "server.ingress.annotations.nginx\\.ingress\\.kubernetes\\.io/ssl-redirect"
    value = "\"true\""
  }

  # ── Metrics ─────────────────────────────────────────────────────────────────
  # ArgoCD exposes Prometheus metrics for the application controller,
  # repo server, and server components. Grafana dashboard ID: 14584.
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

  timeout = 600   # ArgoCD takes longer than most charts to become ready
  atomic  = true
  wait    = true

  depends_on = [var.eks_dependency]
}

# ── ArgoCD AppProject ─────────────────────────────────────────────────────────
#
# Defines what the GuardOps Applications are ALLOWED to do:
#   sourceRepos: only our own repo can be used as a source
#   destinations: only deploy to default and staging namespaces on this cluster
#   clusterResourceWhitelist: allow creating Namespaces (needed for --create-namespace)
#   namespaceResourceWhitelist: allow any resource type in the scoped namespaces
#
# This prevents ArgoCD from accidentally being used to deploy to other clusters
# or namespaces by a misconfigured Application manifest.

resource "kubernetes_manifest" "argocd_project" {
  manifest = {
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "AppProject"
    metadata = {
      name      = "guardops"
      namespace = "argocd"
      labels = {
        "app.kubernetes.io/managed-by" = "terraform"
        "guardops.io/phase"            = "10"
      }
    }
    spec = {
      description = "GuardOps application project — scopes deployable namespaces and source repos"
      sourceRepos = [var.git_repo_url]
      destinations = [
        {
          namespace = "default"
          server    = "https://kubernetes.default.svc"
        },
        {
          namespace = "staging"
          server    = "https://kubernetes.default.svc"
        },
      ]
      # Allow ArgoCD to create/manage Namespaces at the cluster level
      clusterResourceWhitelist = [
        { group = "", kind = "Namespace" },
      ]
      # Allow all resource types within the scoped namespaces
      namespaceResourceWhitelist = [
        { group = "*", kind = "*" },
      ]
    }
  }

  depends_on = [helm_release.argocd]
}

# ── ArgoCD Application — prod ─────────────────────────────────────────────────
#
# Auto-sync DISABLED. The CI pipeline triggers sync explicitly after DAST passes:
#   guardops sync-status --env prod --wait --timeout 300
#
# Values overlay stack (applied in order, last wins):
#   values.yaml               — local/base defaults
#   values-prod.yaml          — prod-specific settings (TLS, replicas, HPA, ZAP)
#   values-override-prod.yaml — auto-generated by guardops deploy (image tag only)

resource "kubernetes_manifest" "app_prod" {
  manifest = {
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "Application"
    metadata = {
      name      = "guardops-app-prod"
      namespace = "argocd"
      labels = {
        "app.kubernetes.io/managed-by" = "terraform"
        "guardops.io/env"              = "prod"
      }
      # Prevent ArgoCD from deleting this Application if the CRD is removed
      finalizers = ["resources-finalizer.argocd.argoproj.io"]
    }
    spec = {
      project = "guardops"
      source = {
        repoURL        = var.git_repo_url
        targetRevision = "HEAD"
        path           = "k8s/helm/guardops-app"
        helm = {
          valueFiles = [
            "values.yaml",
            "values-prod.yaml",
            "values-override-prod.yaml",
          ]
          # releaseName overrides the default (path basename) so the Helm
          # release name matches what deploy_cmd.py / deployer.py create.
          releaseName = "guardops-app"
        }
      }
      destination = {
        server    = "https://kubernetes.default.svc"
        namespace = "default"
      }
      syncPolicy = {
        # No automated block = auto-sync disabled for prod
        syncOptions = [
          "CreateNamespace=true",
          "PrunePropagationPolicy=foreground",
          "ServerSideApply=true",
        ]
        retry = {
          limit = 3
          backoff = {
            duration    = "5s"
            factor      = 2
            maxDuration = "2m"
          }
        }
      }
      # Ignore differences in live image tag — the override file is the
      # source of truth. Without this, ArgoCD would show OutOfSync every
      # time the direct Helm deploy updates the tag before the commit lands.
      ignoreDifferences = [
        {
          group         = "apps"
          kind          = "Deployment"
          jsonPointers  = ["/spec/template/spec/containers/0/image"]
        },
      ]
    }
  }

  depends_on = [
    helm_release.argocd,
    kubernetes_manifest.argocd_project,
  ]
}

# ── ArgoCD Application — staging ──────────────────────────────────────────────
#
# Auto-sync ENABLED. Every commit that modifies values-override-staging.yaml
# is applied to the staging namespace automatically within ~3 minutes.
# selfHeal=true reverts any manual kubectl changes, keeping staging reproducible.

resource "kubernetes_manifest" "app_staging" {
  manifest = {
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "Application"
    metadata = {
      name      = "guardops-app-staging"
      namespace = "argocd"
      labels = {
        "app.kubernetes.io/managed-by" = "terraform"
        "guardops.io/env"              = "staging"
      }
      finalizers = ["resources-finalizer.argocd.argoproj.io"]
    }
    spec = {
      project = "guardops"
      source = {
        repoURL        = var.git_repo_url
        targetRevision = "HEAD"
        path           = "k8s/helm/guardops-app"
        helm = {
          valueFiles = [
            "values.yaml",
            "values-staging.yaml",
            "values-override-staging.yaml",
          ]
          releaseName = "guardops-app-staging"
        }
      }
      destination = {
        server    = "https://kubernetes.default.svc"
        namespace = "staging"
      }
      syncPolicy = {
        automated = {
          prune    = true    # remove resources deleted from Git
          selfHeal = true    # revert manual kubectl changes back to Git state
        }
        syncOptions = [
          "CreateNamespace=true",
          "PrunePropagationPolicy=foreground",
          "ServerSideApply=true",
        ]
        retry = {
          limit = 3
          backoff = {
            duration    = "5s"
            factor      = 2
            maxDuration = "2m"
          }
        }
      }
      ignoreDifferences = [
        {
          group        = "apps"
          kind         = "Deployment"
          jsonPointers = ["/spec/template/spec/containers/0/image"]
        },
      ]
    }
  }

  depends_on = [
    helm_release.argocd,
    kubernetes_manifest.argocd_project,
  ]
}

# infra/terraform/modules/falco/main.tf
#
# GuardOps Phase 7 — Runtime Security (Falco + Loki + Promtail)
#
# This module installs three Helm releases into the EKS cluster:
#   1. Falco      — kernel eBPF sensor + custom GuardOps rules
#   2. Loki       — log aggregation backend
#   3. Promtail   — log shipping DaemonSet (pods → Loki)
#
# Prerequisites (must already exist in the cluster before this module runs):
#   - `monitoring` namespace (created by kube-prometheus-stack)
#   - EBS CSI driver (for Loki PVC — installed by modules/eks)
#
# Why eBPF driver (modern_ebpf) instead of kernel module:
#   EKS AL2023 managed nodes restrict loading out-of-tree kernel modules.
#   The modern eBPF driver runs in userspace (CO-RE) and works on any
#   kernel >= 5.8. EKS 1.28+ uses kernel 6.x, so this is always safe.
#
# The helm and kubernetes providers are configured in the root main.tf
# using data sources from the EKS module outputs.

terraform {
  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.13"
    }
  }
}


# ── Falco ─────────────────────────────────────────────────────────────────────

resource "helm_release" "falco" {
  name       = "falco"
  repository = "https://falcosecurity.github.io/charts"
  chart      = "falco"
  version    = "4.3.0"    # pin — Falco chart updates can change driver defaults

  namespace        = var.monitoring_namespace
  create_namespace = false   # `monitoring` ns already exists from kube-prometheus-stack
  timeout          = 600     # Falco init can be slow on first pod pull

  # ── Driver: modern eBPF ─────────────────────────────────────────────────────
  # No kernel module compilation, works on EKS AL2023 without node taints.
  set {
    name  = "driver.kind"
    value = "modern_ebpf"
  }

  # ── Output: structured JSON ──────────────────────────────────────────────────
  # Required for falco_reader.py to parse priority, rule, output_fields.
  set {
    name  = "falco.json_output"
    value = "true"
  }

  set {
    name  = "falco.json_include_output_property"
    value = "true"
  }

  # ── Log level ────────────────────────────────────────────────────────────────
  # WARNING and above reduces noise in Loki while capturing meaningful events.
  # Change to "notice" if too noisy, "error" for critical-only quiet mode.
  set {
    name  = "falco.log_level"
    value = "warning"
  }

  set {
    name  = "falco.priority"
    value = "warning"
  }

  # ── Prometheus metrics ───────────────────────────────────────────────────────
  # Exposes /metrics on port 8765. The ServiceMonitor below scrapes it.
  set {
    name  = "falco.metrics.enabled"
    value = "true"
  }

  set {
    name  = "falco.metrics.interval"
    value = "15s"
  }

  set {
    name  = "serviceMonitor.create"
    value = "true"
  }

  # ── Custom GuardOps rules ────────────────────────────────────────────────────
  # Reads k8s/falco/custom-rules.yaml at `terraform apply` time and embeds the
  # content as a Helm value. Falco merges custom rules with its default ruleset.
  #
  # NOTE: The key uses a backslash-escaped dot to prevent Helm from splitting
  # "guardops-rules.yaml" into nested YAML keys.
  set {
    name  = "customRules.guardops-rules\\.yaml"
    value = file("${path.root}/../../k8s/falco/custom-rules.yaml")
  }

  # ── Resources ────────────────────────────────────────────────────────────────
  set {
    name  = "resources.requests.memory"
    value = "64Mi"
  }

  set {
    name  = "resources.requests.cpu"
    value = "50m"
  }

  set {
    name  = "resources.limits.memory"
    value = "256Mi"
  }

  set {
    name  = "resources.limits.cpu"
    value = "200m"
  }
}


# ── Loki ──────────────────────────────────────────────────────────────────────

resource "helm_release" "loki" {
  name       = "loki"
  repository = "https://grafana.github.io/helm-charts"
  chart      = "loki"
  version    = "6.6.2"

  namespace        = var.monitoring_namespace
  create_namespace = false
  timeout          = 600

  # Delegate all Loki configuration to the values file so it stays in sync
  # with the manual install path (scripts/setup-runtime-security.ps1).
  values = [
    file("${path.root}/../../k8s/observability/loki-values.yaml")
  ]
}


# ── Promtail ──────────────────────────────────────────────────────────────────

resource "helm_release" "promtail" {
  name       = "promtail"
  repository = "https://grafana.github.io/helm-charts"
  chart      = "promtail"
  version    = "6.15.5"

  namespace        = var.monitoring_namespace
  create_namespace = false
  timeout          = 300

  values = [
    file("${path.root}/../../k8s/observability/promtail-values.yaml")
  ]

  # Loki must exist before Promtail tries to push logs to it.
  depends_on = [helm_release.loki]
}

# infra/terraform/modules/eks/main.tf
#
# PHASE 4B — Minimum-cost EKS cluster for GuardOps portfolio testing.
#
# Cost-saving decisions vs a full production cluster:
#   - Single t3.medium node instead of multi-node (saves ~$60/month)
#   - CloudWatch logging disabled by default (saves ~$0.50/GB ingested)
#   - EBS CSI driver removed (not needed for stateless test-app)
#   - Single AZ node placement (NAT gateway cost controlled at VPC level)
#
# ALWAYS run `terraform destroy` when done for the day.
# EKS control plane alone costs $0.10/hour even with zero nodes.

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  # Conditionally enable CloudWatch logs — empty list = disabled = free
  log_types = var.enable_cloudwatch_logs ? ["api", "audit", "authenticator"] : []
}

# ── EKS Cluster (control plane) ───────────────────────────────────────────────
resource "aws_eks_cluster" "main" {
  name     = "${local.name_prefix}-cluster"
  role_arn = var.cluster_role_arn
  version  = "1.31"

  vpc_config {
    subnet_ids = concat(var.private_subnet_ids, var.public_subnet_ids)

    # Public access lets you run kubectl from your Windows machine.
    # In real prod you'd disable this and use a VPN/bastion.
    endpoint_private_access = true
    endpoint_public_access  = true
  }

  # Disabled by default to avoid CloudWatch costs during dev/testing.
  # Set enable_cloudwatch_logs = true in tfvars when you need audit logs.
  enabled_cluster_log_types = local.log_types

  depends_on = [var.cluster_role_arn]

  tags = { Name = "${local.name_prefix}-cluster" }
}

# ── Managed Node Group ────────────────────────────────────────────────────────
resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${local.name_prefix}-nodes"
  node_role_arn   = var.node_role_arn
  subnet_ids      = var.private_subnet_ids
  instance_types  = [var.node_instance_type]

  scaling_config {
    min_size     = var.node_min_size
    max_size     = var.node_max_size
    desired_size = var.node_desired_size
  }

  update_config { max_unavailable = 1 }

  labels = {
    role        = "worker"
    environment = var.environment
  }

  tags = { Name = "${local.name_prefix}-node-group" }
}

# ── EKS Add-ons ───────────────────────────────────────────────────────────────
# Only the three essential add-ons. EBS CSI removed — test-app is stateless.

resource "aws_eks_addon" "coredns" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "coredns"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.main]
}

resource "aws_eks_addon" "kube_proxy" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "kube-proxy"
  resolve_conflicts_on_update = "OVERWRITE"
}

resource "aws_eks_addon" "vpc_cni" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "vpc-cni"
  resolve_conflicts_on_update = "OVERWRITE"
}

# PHASE 5: Uncomment when adding Prometheus/Grafana (needs persistent storage)
# resource "aws_eks_addon" "ebs_csi" {
#   cluster_name                = aws_eks_cluster.main.name
#   addon_name                  = "aws-ebs-csi-driver"
#   resolve_conflicts_on_update = "OVERWRITE"
#   depends_on                  = [aws_eks_node_group.main]
# }

# infra/terraform/modules/eks/main.tf
#
# PHASE 5 — EBS CSI driver enabled for Prometheus/Grafana persistent storage.
#
# Changes from Phase 4B:
#   - aws_eks_addon.ebs_csi uncommented (required for PersistentVolumeClaims)
#   - No other changes — VPC, cluster, node group are identical
#
# Cost-saving decisions vs a full production cluster:
#   - Single t3.large node (upgraded from t3.medium for Prometheus headroom)
#   - CloudWatch logging disabled by default (saves ~$0.50/GB ingested)
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

resource "aws_launch_template" "nodes" {
  name_prefix = "${local.name_prefix}-node-"

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"   # keeps IMDSv2 enforced (security best practice)
    http_put_response_hop_limit = 2            # allows pods to reach IMDS for AWS SDK auth
  }

  tag_specifications {
    resource_type = "instance"
    tags = { Name = "${local.name_prefix}-node" }
  }
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

  launch_template {
    id      = aws_launch_template.nodes.id
    version = aws_launch_template.nodes.latest_version
  }

  tags = { Name = "${local.name_prefix}-node-group" }
}

# ── EKS Add-ons ───────────────────────────────────────────────────────────────

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

# PHASE 5: EBS CSI driver — required for Prometheus and Grafana PersistentVolumeClaims.
# The node role already has the AmazonEBSCSIDriverPolicy attached via the IAM module.
# If terraform plan says the node role is missing the policy, add it to modules/iam/main.tf:
#   data "aws_iam_policy" "ebs_csi" { arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy" }
#   resource "aws_iam_role_policy_attachment" "ebs_csi" { role = aws_iam_role.node.name; policy_arn = data.aws_iam_policy.ebs_csi.arn }
resource "aws_eks_addon" "ebs_csi" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "aws-ebs-csi-driver"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.main]
}

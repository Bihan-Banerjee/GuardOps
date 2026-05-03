# infra/terraform/modules/eks/main.tf
#
# Creates a managed EKS cluster with:
#   - Managed node group (AWS handles node lifecycle, patching, replacement)
#   - Nodes in private subnets only
#   - EKS add-ons: CoreDNS, kube-proxy, VPC CNI, EBS CSI driver
#
# After terraform apply, run:
#   aws eks update-kubeconfig --region <region> --name <cluster-name>
# to configure kubectl to talk to this cluster.

locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

# ---------------------------------------------------------------------------
# EKS Cluster (control plane — managed by AWS)
# ---------------------------------------------------------------------------
resource "aws_eks_cluster" "main" {
  name     = "${local.name_prefix}-cluster"
  role_arn = var.cluster_role_arn
  version  = "1.31"

  vpc_config {
    subnet_ids              = var.private_subnet_ids
    endpoint_private_access = true   # kubectl from within VPC works
    endpoint_public_access  = true   # kubectl from your laptop works
    # In a hardened setup, set endpoint_public_access = false and use
    # a VPN or bastion host to reach the API server.
  }

  # Enable control plane logging to CloudWatch
  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  depends_on = [var.cluster_role_arn]
}

# ---------------------------------------------------------------------------
# Managed Node Group (EC2 worker nodes)
# ---------------------------------------------------------------------------
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

  # Rolling update: replace nodes one at a time
  # max_unavailable = 1 means one node is replaced at a time
  update_config { max_unavailable = 1 }

  labels = {
    role        = "worker"
    environment = var.environment
  }

  tags = { Name = "${local.name_prefix}-node-group" }
}

# ---------------------------------------------------------------------------
# EKS Add-ons (AWS-managed cluster components)
# ---------------------------------------------------------------------------

# CoreDNS: cluster-internal DNS resolution (pod → service name lookup)
resource "aws_eks_addon" "coredns" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "coredns"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.main]
}

# kube-proxy: maintains network rules on each node for Service routing
resource "aws_eks_addon" "kube_proxy" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "kube-proxy"
  resolve_conflicts_on_update = "OVERWRITE"
}

# VPC CNI: assigns VPC IP addresses directly to pods
resource "aws_eks_addon" "vpc_cni" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "vpc-cni"
  resolve_conflicts_on_update = "OVERWRITE"
}

# EBS CSI Driver: allows pods to use EBS volumes as persistent storage
# Required if any workload (e.g. Prometheus) needs persistent storage
resource "aws_eks_addon" "ebs_csi" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "aws-ebs-csi-driver"
  resolve_conflicts_on_update = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.main]
}

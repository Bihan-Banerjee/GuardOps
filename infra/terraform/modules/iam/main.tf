# infra/terraform/modules/iam/main.tf
#
# Phase 5 change: added aws_iam_role_policy_attachment.ebs_csi_policy
#
# The EBS CSI driver addon (enabled in modules/eks/main.tf) runs as a DaemonSet
# on every node and calls AWS APIs to create/attach/detach EBS volumes on behalf
# of PersistentVolumeClaims. Without AmazonEBSCSIDriverPolicy on the node role,
# it authenticates fine but gets AccessDenied on ec2:CreateVolume — PVCs stay
# Pending forever with no obvious error in the pod logs.
#
# This policy attachment is the ONLY change from Phase 4B.
# (The legacy static CI user was removed in Phase 6/11 — see section 3 below;
# CI now authenticates via GitHub OIDC in modules/iam_oidc.)

locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

# ── 1. EKS Cluster Role ───────────────────────────────────────────────────────

resource "aws_iam_role" "eks_cluster" {
  name = "${local.name_prefix}-eks-cluster-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = { Name = "${local.name_prefix}-eks-cluster-role" }
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

# ── 2. EKS Node Role ──────────────────────────────────────────────────────────

resource "aws_iam_role" "eks_node" {
  name = "${local.name_prefix}-eks-node-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = { Name = "${local.name_prefix}-eks-node-role" }
}

resource "aws_iam_role_policy_attachment" "eks_worker_node_policy" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "eks_ecr_read_policy" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy_attachment" "eks_cni_policy" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

# PHASE 5: Required for Prometheus and Grafana PersistentVolumeClaims.
# The EBS CSI driver calls ec2:CreateVolume, ec2:AttachVolume, ec2:DescribeVolumes
# etc. on behalf of PVCs. Without this policy the driver gets AccessDenied and
# every PVC stays Pending indefinitely.
resource "aws_iam_role_policy_attachment" "ebs_csi_policy" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

# ── 3. CI/CD User — REMOVED (Phase 6/11) ─────────────────────────────────────
#
# The legacy static IAM user (guardops-prod-ci-user) with long-lived access keys
# was superseded by GitHub OIDC in Phase 6 (modules/iam_oidc). CI assumes the
# github_actions role via OIDC and no longer uses AWS_ACCESS_KEY_ID/SECRET, so
# the user is intentionally removed here. This also clears the EntityAlreadyExists
# 409 that blocked morning-start.ps1 when the orphaned user existed in AWS but not
# in Terraform state, and removes unused long-lived credentials (security).
#
# If the old user still exists in your account, delete it once:
#   aws iam list-access-keys   --user-name guardops-prod-ci-user
#   aws iam delete-access-key  --user-name guardops-prod-ci-user --access-key-id <id>
#   aws iam delete-user-policy --user-name guardops-prod-ci-user --policy-name guardops-prod-ci-policy
#   aws iam delete-user        --user-name guardops-prod-ci-user



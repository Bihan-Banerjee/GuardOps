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
# Everything else (cluster role, node role, CI user) is identical.

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

# ── 3. CI/CD User ─────────────────────────────────────────────────────────────

resource "aws_iam_user" "ci" {
  name = "${local.name_prefix}-ci-user"
  tags = { Name = "${local.name_prefix}-ci-user", Purpose = "github-actions" }
}

resource "aws_iam_user_policy" "ci_policy" {
  name = "${local.name_prefix}-ci-policy"
  user = aws_iam_user.ci.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [

      {
        Sid      = "ECRAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "ECRPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeRepositories",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
          "ecr:CreateRepository",
          "ecr:PutLifecyclePolicy",
        ]
        Resource = "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/*"
      },

      {
        Sid    = "S3Reports"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:ListBucket",
        ]
        Resource = [
          "arn:aws:s3:::${var.project_name}-reports-${var.aws_account_id}",
          "arn:aws:s3:::${var.project_name}-reports-${var.aws_account_id}/*",
        ]
      },

      {
        Sid    = "EKSDeploy"
        Effect = "Allow"
        Action = [
          "eks:DescribeCluster",
          "eks:ListClusters",
        ]
        Resource = "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:cluster/*"
      },
    ]
  })
}

resource "aws_iam_access_key" "ci" {
  user = aws_iam_user.ci.name
}

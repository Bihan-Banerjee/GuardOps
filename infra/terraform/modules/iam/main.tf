# infra/terraform/modules/iam/main.tf
#
# Creates IAM roles and policies following least-privilege:
#   - EKS cluster role:   AWS manages the control plane with this role
#   - EKS node role:      Worker nodes use this to pull images, write logs
#   - CI/CD user:         GitHub Actions uses this — ECR push + S3 + EKS deploy only
#
# LEAST PRIVILEGE PRINCIPLE:
#   Every role has exactly the permissions it needs — nothing more.
#   The CI user cannot create/delete EC2 instances, modify IAM, or access
#   anything outside ECR, S3, and EKS. If the CI credentials are leaked,
#   the blast radius is limited.

locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

# ---------------------------------------------------------------------------
# EKS Cluster Role — used by the EKS control plane
# ---------------------------------------------------------------------------
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
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

# ---------------------------------------------------------------------------
# EKS Node Role — used by EC2 worker nodes
# ---------------------------------------------------------------------------
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
}

resource "aws_iam_role_policy_attachment" "eks_worker_node" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "eks_cni" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

# Nodes need ECR read access to pull application images
resource "aws_iam_role_policy_attachment" "eks_ecr_read" {
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# ---------------------------------------------------------------------------
# CI/CD IAM User — used by GitHub Actions
# ---------------------------------------------------------------------------
resource "aws_iam_user" "ci" {
  name = "${local.name_prefix}-ci"
  tags = { Purpose = "GitHub Actions CI/CD pipeline" }
}

resource "aws_iam_access_key" "ci" {
  user = aws_iam_user.ci.name
}

# ECR permissions: authenticate, push images, create repository if missing
resource "aws_iam_policy" "ci_ecr" {
  name        = "${local.name_prefix}-ci-ecr-policy"
  description = "Allows CI to push images to ECR"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRAuth"
        Effect = "Allow"
        Action = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "ECRPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
          "ecr:DescribeRepositories",
          "ecr:CreateRepository",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = var.ecr_arn
      }
    ]
  })
}

# S3 permissions: upload security reports only
resource "aws_iam_policy" "ci_s3" {
  name        = "${local.name_prefix}-ci-s3-policy"
  description = "Allows CI to upload security reports to S3"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "S3Reports"
      Effect = "Allow"
      Action = [
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket",
      ]
      Resource = [
        var.s3_bucket_arn,
        "${var.s3_bucket_arn}/*",
      ]
    }]
  })
}

# EKS permissions: update kubeconfig + deploy via helm/kubectl
resource "aws_iam_policy" "ci_eks" {
  name        = "${local.name_prefix}-ci-eks-policy"
  description = "Allows CI to deploy to EKS"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "EKSDeploy"
      Effect = "Allow"
      Action = [
        "eks:DescribeCluster",
        "eks:ListClusters",
      ]
      Resource = "arn:aws:eks:*:${var.aws_account_id}:cluster/${var.project_name}-*"
    }]
  })
}

resource "aws_iam_user_policy_attachment" "ci_ecr" {
  user       = aws_iam_user.ci.name
  policy_arn = aws_iam_policy.ci_ecr.arn
}

resource "aws_iam_user_policy_attachment" "ci_s3" {
  user       = aws_iam_user.ci.name
  policy_arn = aws_iam_policy.ci_s3.arn
}

resource "aws_iam_user_policy_attachment" "ci_eks" {
  user       = aws_iam_user.ci.name
  policy_arn = aws_iam_policy.ci_eks.arn
}

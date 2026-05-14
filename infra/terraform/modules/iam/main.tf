# infra/terraform/modules/iam/main.tf
#
# Creates three IAM identities for GuardOps:
#
#   1. EKS Cluster Role   — used by the EKS control plane to manage AWS resources
#   2. EKS Node Role      — used by EC2 worker nodes to pull images, write logs
#   3. CI/CD User         — used by GitHub Actions to push to ECR, upload to S3,
#                           and deploy to EKS (least-privilege)
#
# All roles/policies follow least-privilege: only the exact actions needed.

locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

# ── 1. EKS Cluster Role ───────────────────────────────────────────────────────
# The EKS control plane assumes this role to call AWS APIs on your behalf
# (e.g. creating load balancers, describing EC2 instances for node registration).

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

# AWS-managed policy that grants EKS the minimum permissions it needs
resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

# ── 2. EKS Node Role ──────────────────────────────────────────────────────────
# EC2 worker nodes assume this role. They need it to:
#   - Register with the EKS cluster (EKSWorkerNodePolicy)
#   - Pull container images from ECR (AmazonEC2ContainerRegistryReadOnly)
#   - Set up pod networking (AmazonEKS_CNI_Policy)

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

# ── 3. CI/CD User ─────────────────────────────────────────────────────────────
# Used by GitHub Actions. Least-privilege: only what the pipeline actually needs.
# NEVER give this user AdministratorAccess.

resource "aws_iam_user" "ci" {
  name = "${local.name_prefix}-ci-user"
  tags = { Name = "${local.name_prefix}-ci-user", Purpose = "github-actions" }
}

# Inline policy: exactly what CI needs and nothing more
resource "aws_iam_user_policy" "ci_policy" {
  name = "${local.name_prefix}-ci-policy"
  user = aws_iam_user.ci.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [

      # ECR: authenticate, push images, create repos
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

      # S3: upload scan reports to the reports bucket only
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

      # EKS: update kubeconfig and deploy via Helm
      # PHASE 4B: needed for CI deploy job
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

# Access key for the CI user — used in GitHub Actions secrets
# IMPORTANT: After terraform apply, run:
#   terraform output ci_user_access_key_id
#   terraform output -raw ci_user_secret_access_key
# and update your GitHub secrets with these values.
#
# These are marked sensitive so they don't print in plain text during apply.
resource "aws_iam_access_key" "ci" {
  user = aws_iam_user.ci.name
}

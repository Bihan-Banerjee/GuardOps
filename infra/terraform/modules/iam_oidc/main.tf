# infra/terraform/modules/iam_oidc/main.tf
#
# Phase 6 — GitHub OIDC: replaces the long-lived IAM user CI credentials
# with short-lived tokens that GitHub Actions exchanges for AWS access.
#
# What this module creates:
#   1. aws_iam_openid_connect_provider  — registers GitHub's OIDC IdP with AWS
#   2. aws_iam_role (github_actions)    — the role CI runners assume
#   3. aws_iam_role_policy (ci_policy)  — least-privilege inline policy
#
# How it works at runtime:
#   GitHub generates a JWT for each workflow run, scoped to the repo and ref.
#   The runner calls sts:AssumeRoleWithWebIdentity and exchanges the JWT for
#   temporary credentials (valid ~1 hour). No static secrets involved.
#
# Trust conditions (both must match):
#   aud = "sts.amazonaws.com"                     — prevents confused-deputy attacks
#   sub LIKE "repo:<org>/<repo>:*"                — locks role to your repo only
#
# To tighten further (recommended once pipelines are stable):
#   Change sub to "repo:<org>/<repo>:ref:refs/heads/main"
#   This restricts AWS access to pushes on main only, not PRs or other branches.

# ── OIDC Identity Provider ────────────────────────────────────────────────────

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  # AWS STS is the audience — required for AssumeRoleWithWebIdentity
  client_id_list = ["sts.amazonaws.com"]

  # Two thumbprints cover GitHub's current and previous intermediate CA.
  # AWS validates against its own CA store since Oct 2023 — these are still
  # required by the Terraform resource but are no longer the trust anchor.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]

  tags = {
    Name    = "${var.project_name}-github-oidc-provider"
    Phase   = "6"
    Purpose = "github-actions-ci"
  }
}

# ── IAM Role ──────────────────────────────────────────────────────────────────

resource "aws_iam_role" "github_actions" {
  name        = "${var.project_name}-github-actions-role"
  description = "Assumed by GitHub Actions via OIDC - no static credentials"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "GitHubOIDCAssumeRole"
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            # Audience must be sts.amazonaws.com — set by configure-aws-credentials action
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            # Scope to your repo — e.g. "Bihan-Banerjee/GuardOps:*"
            # The "*" allows any branch, tag, or PR. Tighten to ":ref:refs/heads/main"
            # once you're happy the pipeline works.
            "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:*"
          }
        }
      }
    ]
  })

  # Max session duration for CI runs. 1 hour is plenty.
  max_session_duration = 3600

  tags = {
    Name    = "${var.project_name}-github-actions-role"
    Phase   = "6"
    Purpose = "github-actions-ci"
  }
}

# ── Inline Policy — Least Privilege ──────────────────────────────────────────
#
# Mirrors exactly what the old CI IAM user could do.
# Sections are labelled so it's obvious what each permission is for.

# nosemgrep: no-iam-creds-exposure
resource "aws_iam_role_policy" "ci_policy" {
  name = "${var.project_name}-ci-policy"
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [

      # ── ECR: authenticate, push, and pull images ───────────────────────────
      # GetAuthorizationToken is account-scoped (no resource restriction possible)
      {
        Sid    = "ECRAuth"
        Effect = "Allow"
        Action = ["ecr:GetAuthorizationToken"]
        Resource = ["*"]
      },
      {
        Sid    = "ECRReadWrite"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:DescribeRepositories",
          "ecr:CreateRepository",
          "ecr:DescribeImages",
          "ecr:ListImages",
        ]
        Resource = [
          "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/${var.project_name}*",
          "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/guardops*",
        ]
      },

      # ── EKS: generate kubeconfig ──────────────────────────────────────────
      # aws eks update-kubeconfig only needs DescribeCluster
      {
        Sid    = "EKSDescribe"
        Effect = "Allow"
        Action = [
          "ecr:DescribeCluster",
          "eks:DescribeCluster",
          "eks:ListClusters",
        ]
        Resource = [
          "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:cluster/${var.project_name}*",
        ]
      },

      # ── S3: write scan reports ────────────────────────────────────────────
      {
        Sid    = "S3Reports"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:ListBucket",
        ]
        Resource = [
          "arn:aws:s3:::guardops-reports-*",
          "arn:aws:s3:::guardops-reports-*/*",
        ]
      },

      # ── S3: read remote Terraform state (for plan/apply in CI if needed) ──
      # Narrow to the tfstate bucket only — write is not granted deliberately.
      {
        Sid    = "TerraformStateRead"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
        ]
        Resource = [
          "arn:aws:s3:::guardops-tfstate-*",
          "arn:aws:s3:::guardops-tfstate-*/*",
        ]
      },
    ]
  })
}

# ── EBS CSI policy attachment ─────────────────────────────────────────────────
# Only needed if this role is also used for node operations.
# For a pure CI role this is not required — kept here commented out as reference.
#
# resource "aws_iam_role_policy_attachment" "ebs_csi" {
#   role       = aws_iam_role.github_actions.name
#   policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
# }


# infra/terraform/modules/s3/main.tf
#
# Creates an S3 bucket for storing GuardOps security scan reports.
#
# Free tier: 5 GB storage, 20,000 GET requests, 2,000 PUT requests per month.
# Scan reports are tiny (a few KB of JSON/HTML each), so free tier is plenty.
#
# Security settings:
#   - Block all public access (reports should never be public)
#   - AES256 server-side encryption (free)
#   - Versioning enabled (lets you recover overwritten reports)
#   - Lifecycle: move to Glacier after 90 days (saves storage cost for old reports)

locals {
  # Bucket names must be globally unique across all AWS accounts.
  # Appending account ID makes it unique without needing a random suffix.
  bucket_name = "${var.project_name}-reports-${var.aws_account_id}"
}

resource "aws_s3_bucket" "reports" {
  bucket = local.bucket_name

  # Terraform will refuse to destroy a non-empty bucket by default.
  # Set to true ONLY if you want `terraform destroy` to delete all reports too.
  # Leave as false for safety.
  force_destroy = false

  tags = {
    Name    = local.bucket_name
    Purpose = "scan-reports"
  }
}

# Block public access. ACL-based public access is always blocked. The two
# policy-related flags are relaxed ONLY when enable_public_snapshot is true, so a
# bucket policy can expose the dashboard/* prefix (and nothing else) for the SPA's
# offline snapshot fallback. Default (false) keeps the historical "all blocked".
resource "aws_s3_bucket_public_access_block" "reports" {
  bucket = aws_s3_bucket.reports.id

  block_public_acls  = true
  ignore_public_acls = true
  # Intentional: only relaxed when enable_public_snapshot=true, and only the
  # dashboard/* prefix is then made public (see the scoped policy below).
  block_public_policy     = var.enable_public_snapshot ? false : true # nosemgrep
  restrict_public_buckets = var.enable_public_snapshot ? false : true # nosemgrep
}

# Public read for the snapshot object only — scoped to dashboard/*. reports/ and
# metadata/ remain private. See variable "enable_public_snapshot".
data "aws_iam_policy_document" "public_snapshot" {
  count = var.enable_public_snapshot ? 1 : 0

  # Intentional public read, scoped to the dashboard/* snapshot prefix only — this
  # is the opt-in offline fallback for the public SPA. nosemgrep on the wildcard
  # principal: the resource ARN restricts it to dashboard/*, nothing else.
  statement {
    sid       = "PublicReadDashboardSnapshot"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.reports.arn}/dashboard/*"]

    principals {
      type        = "*"
      identifiers = ["*"] # nosemgrep
    }
  }
}

resource "aws_s3_bucket_policy" "public_snapshot" {
  count  = var.enable_public_snapshot ? 1 : 0
  bucket = aws_s3_bucket.reports.id
  policy = data.aws_iam_policy_document.public_snapshot[0].json

  # The policy can't be applied until the access block stops restricting it.
  depends_on = [aws_s3_bucket_public_access_block.reports]
}

# The SPA fetches the snapshot cross-origin (from dashboard.guardops.live), so the
# browser needs the bucket to return CORS headers on the GET.
resource "aws_s3_bucket_cors_configuration" "public_snapshot" {
  count  = var.enable_public_snapshot ? 1 : 0
  bucket = aws_s3_bucket.reports.id

  cors_rule {
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = var.snapshot_cors_origins
    allowed_headers = ["*"]
    max_age_seconds = 300
  }
}

# AES256 encryption at rest — free, unlike KMS (~$1/month)
# PHASE 4B: switch to aws:kms for compliance if needed
resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = false
  }
}

# Versioning: keeps previous versions of reports.
# Useful if a CI run overwrites a report and you need the old one.
resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Lifecycle: move old reports to Glacier after 90 days.
# Glacier costs ~$0.004/GB/month vs S3's ~$0.023/GB/month.
# For tiny scan reports this barely matters, but it's good practice.
resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  # Must wait for versioning to be configured first
  depends_on = [aws_s3_bucket_versioning.reports]

  bucket = aws_s3_bucket.reports.id

  rule {
    id     = "archive-old-reports"
    status = "Enabled"

    filter {
      prefix = "reports/"
    }

    # Move current version to Glacier after 90 days
    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    # Move non-current (versioned) objects to Glacier after 30 days
    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "GLACIER"
    }

    # Permanently delete non-current versions after 365 days
    # (prevents unlimited version accumulation)
    noncurrent_version_expiration {
      noncurrent_days = 365
    }
  }
}

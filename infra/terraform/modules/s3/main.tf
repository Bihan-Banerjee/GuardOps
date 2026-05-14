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

# Block all public access — reports must never be publicly readable
resource "aws_s3_bucket_public_access_block" "reports" {
  bucket = aws_s3_bucket.reports.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
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

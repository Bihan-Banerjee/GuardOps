# infra/terraform/modules/s3/main.tf
#
# Creates S3 buckets for:
#   - Security scan reports (HTML + JSON from every guardops deploy)
#   - Terraform state (backend bucket — created manually before terraform init)
#
# WHY store reports in S3:
#   Every deploy generates a timestamped security report. Storing them in S3
#   gives you a permanent audit trail: which commit, which scan, which CVEs
#   were present, which were suppressed. Required for compliance in prod.

locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

resource "aws_s3_bucket" "reports" {
  bucket = "${local.name_prefix}-reports-${var.aws_region}"
  tags   = { Name = "${local.name_prefix}-reports" }
}

# Block all public access — reports contain security findings and must not be public
resource "aws_s3_bucket_public_access_block" "reports" {
  bucket = aws_s3_bucket.reports.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enable versioning — lets you recover accidentally deleted reports
resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id
  versioning_configuration { status = "Enabled" }
}

# Server-side encryption at rest
resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Lifecycle: move reports older than 90 days to Glacier (cheap long-term storage)
# Delete after 365 days unless compliance requires longer retention
resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    id     = "archive-old-reports"
    status = "Enabled"
    transition {
      days          = 90
      storage_class = "GLACIER"
    }
    expiration { days = 365 }
  }
}

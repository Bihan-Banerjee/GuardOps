# infra/terraform/modules/s3/outputs.tf

output "reports_bucket_name" {
  description = "S3 bucket name. Set this as GUARDOPS_S3_BUCKET in GitHub Secrets."
  value       = aws_s3_bucket.reports.bucket
}

output "reports_bucket_arn" {
  description = "S3 bucket ARN. Used in IAM policies — needed in Phase 4B."
  value       = aws_s3_bucket.reports.arn
}

output "reports_bucket_region" {
  description = "Region the bucket was created in."
  value       = aws_s3_bucket.reports.region
}

# v1.0.0: the public URL the SPA reads when the live API is down. Empty unless
# enable_public_snapshot is true. Set this as VITE_SNAPSHOT_URL in the web build.
output "dashboard_snapshot_url" {
  description = "Public URL of the dashboard snapshot (offline SPA fallback)."
  value       = var.enable_public_snapshot ? "https://${aws_s3_bucket.reports.bucket}.s3.${aws_s3_bucket.reports.region}.amazonaws.com/dashboard/snapshot.json" : ""
}

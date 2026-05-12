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

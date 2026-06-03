# infra/terraform/modules/s3/variables.tf

variable "project_name" {
  description = "Project name prefix. Used in bucket name."
  type        = string
}

variable "environment" {
  description = "Deployment environment."
  type        = string
}

variable "aws_region" {
  description = "AWS region where the bucket lives."
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID. Appended to bucket name to guarantee global uniqueness."
  type        = string
}

# v1.0.0: the public dashboard snapshot (offline fallback for dashboard.guardops.live).
# When true, ONLY the dashboard/* prefix becomes publicly readable so the SPA can
# fetch dashboard/snapshot.json without credentials while the cluster is down. The
# reports/ and metadata/ prefixes stay private. Off by default — enabling this makes
# scan-finding summaries in the snapshot publicly visible, so it's a conscious choice.
variable "enable_public_snapshot" {
  description = "Expose only the dashboard/* prefix for the public SPA snapshot fallback."
  type        = bool
  default     = false
}

variable "snapshot_cors_origins" {
  description = "Origins allowed to fetch the public snapshot via the browser (CORS)."
  type        = list(string)
  default = [
    "https://dashboard.guardops.live",
    "https://guardops.live",
    "https://www.guardops.live",
    "http://localhost:5173",
    "http://localhost:4173",
  ]
}

# infra/terraform/bootstrap/main.tf
#
# ONE-TIME SETUP — Creates the S3 bucket and DynamoDB table that your main
# Terraform config will use for remote state. This bootstrap config itself
# uses local state — that is intentional. Losing the state of a bucket/table
# is fine because you can always import them back in one command.
#
# ── Execution order ───────────────────────────────────────────────────────────
#
#   Step 1 — Run this bootstrap (one time only):
#     cd infra/terraform/bootstrap
#     terraform init
#     terraform apply -auto-approve
#
#   Step 2 — Migrate your existing local state to S3:
#     cd infra/terraform
#     terraform init -migrate-state
#     (Terraform will ask "Do you want to copy existing state?" — answer yes)
#
#   Step 3 — Verify migration worked:
#     terraform state list
#     (Should show all your existing resources, now stored in S3)
#
#   Step 4 — Delete local state files (no longer needed):
#     Remove-Item terraform.tfstate, terraform.tfstate.backup -ErrorAction SilentlyContinue
#
# ── What gets created ─────────────────────────────────────────────────────────
#
#   S3 bucket    : guardops-tfstate-236796665744
#                  versioning ON  — every state change is recoverable
#                  AES256         — state files are encrypted at rest
#                  public access BLOCKED — state contains sensitive outputs
#
#   DynamoDB     : guardops-tf-lock
#                  PAY_PER_REQUEST — costs ~$0 unless you run many concurrent applies
#                  LockID (String) — Terraform acquires this before writing state,
#                                    preventing two engineers (or two CI jobs) from
#                                    corrupting state simultaneously
#
# ── Cost ─────────────────────────────────────────────────────────────────────
#   S3: ~$0.02/month for a few state files
#   DynamoDB: ~$0.00 (PAY_PER_REQUEST, locks are tiny and infrequent)

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Intentionally NO backend block here — bootstrap uses local state
}

provider "aws" {
  region = "ap-south-1"
}

# ── S3 bucket for Terraform state ─────────────────────────────────────────────

resource "aws_s3_bucket" "tfstate" {
  bucket = "guardops-tfstate-236796665744"

  # prevent_destroy stops `terraform destroy` from deleting your state bucket.
  # You would have to manually remove this lifecycle block first.
  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name      = "guardops-terraform-state"
    ManagedBy = "terraform-bootstrap"
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── DynamoDB table for state locking ──────────────────────────────────────────

# nosemgrep: aws-dynamodb-table-unencrypted
resource "aws_dynamodb_table" "tf_lock" {
  name         = "guardops-tf-lock"
  billing_mode = "PAY_PER_REQUEST"   # no provisioned capacity cost
  hash_key     = "LockID"            # exactly what Terraform expects

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Name      = "guardops-terraform-lock"
    ManagedBy = "terraform-bootstrap"
  }
}

# ── Outputs — paste these into main.tf backend block ──────────────────────────

output "state_bucket_name" {
  value = aws_s3_bucket.tfstate.bucket
}

output "lock_table_name" {
  value = aws_dynamodb_table.tf_lock.name
}


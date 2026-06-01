# infra/terraform/modules/ecr/main.tf
#
# Creates ECR (Elastic Container Registry) repositories.
#
# Free tier: 500 MB storage per month per account.
# Each image layer is deduplicated, so in practice one small Python image
# (~150 MB compressed) uses well under the free limit.
#
# scan_on_push: AWS scans every image for CVEs automatically, for free.
# lifecycle_policy: cosign-aware retention — prevents runaway storage costs
#   without expiring image signatures (Phase 11).

locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

resource "aws_ecr_repository" "repos" {
  # Creates one repository per name in var.image_names
  for_each = toset(var.image_names)

  name                 = each.value
  # nosemgrep: aws-ecr-mutable-image-tags
  image_tag_mutability = "MUTABLE"   # allows pushing :latest tag repeatedly

  # Free AWS-managed vulnerability scanning on every push
  image_scanning_configuration {
    scan_on_push = true
  }

  # AES256 encryption is free. KMS would cost ~$1/month — not worth it for a portfolio project.
  # PHASE 4B: switch to KMS for production-grade compliance
  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Name = "${local.name_prefix}-${each.value}"
  }
}

# Cosign-aware retention (Phase 11).
#
# Without a lifecycle policy, images accumulate and eventually exceed the 500 MB
# free tier. The naive "keep last N, tagStatus=any" rule is dangerous once images
# are signed: cosign stores each signature/attestation as its OWN tagged image
# (sha256-<digest>.sig / .att), so an aggressive count limit can expire an
# image's signature out from under it and later break Kyverno verifyImages.
#
# ECR cannot pin a signature to its subject image, so the standard workaround is:
#   1. Expire UNTAGGED images quickly (these are never signatures — .sig/.att are
#      tagged — so this only reaps genuinely orphaned layers).
#   2. Keep a generous count of the most-recent images (tagStatus=any) so the
#      active image set AND the signatures pushed alongside them stay together.
resource "aws_ecr_lifecycle_policy" "repos" {
  for_each   = aws_ecr_repository.repos
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Keep last 25 images (incl. cosign .sig/.att artifacts)"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 25
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}


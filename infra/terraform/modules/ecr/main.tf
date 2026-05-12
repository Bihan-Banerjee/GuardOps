# infra/terraform/modules/ecr/main.tf
#
# Creates ECR (Elastic Container Registry) repositories.
#
# Free tier: 500 MB storage per month per account.
# Each image layer is deduplicated, so in practice one small Python image
# (~150 MB compressed) uses well under the free limit.
#
# scan_on_push: AWS scans every image for CVEs automatically, for free.
# lifecycle_policy: keeps only the last 10 images — prevents runaway storage costs.

locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

resource "aws_ecr_repository" "repos" {
  # Creates one repository per name in var.image_names
  for_each = toset(var.image_names)

  name                 = each.value
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

# Keep only the last 10 images in each repo.
# Without this, old images accumulate and eventually exceed the 500 MB free tier.
resource "aws_ecr_lifecycle_policy" "repos" {
  for_each   = aws_ecr_repository.repos
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 10 images, expire older ones"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

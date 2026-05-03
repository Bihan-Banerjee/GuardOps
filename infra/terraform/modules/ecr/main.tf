# infra/terraform/modules/ecr/main.tf
#
# Creates a private ECR repository with:
#   - Scan on push: Trivy/ECR native scanner runs on every pushed image
#   - Lifecycle policy: keeps only the last 30 images to control storage costs
#   - Image tag immutability: MUTABLE allows overwriting tags (simpler for dev)
#     Set to IMMUTABLE in prod once you adopt strict tagging.

locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

resource "aws_ecr_repository" "app" {
  name                 = "${local.name_prefix}-app"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    # ECR native scanning on every push — complements Trivy in the pipeline
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = { Name = "${local.name_prefix}-ecr" }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 30 images — remove older ones to control storage costs"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 30
        }
        action = { type = "expire" }
      }
    ]
  })
}

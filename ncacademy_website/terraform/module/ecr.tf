resource "aws_ecr_repository" "ncacademy_ecr_repo" {
  name = "${var.project_name}-repo-${var.env}"
  tags = {
    Environment = terraform.workspace
    Project     = var.project_name
  }

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "ncacademy_ecr_repo_policy" {
  repository = aws_ecr_repository.ncacademy_ecr_repo.name
  policy     = <<EOF
{
    "rules": [
        {
            "rulePriority": 1,
            "description": "Keep last 5 images",
            "selection": {
                "tagStatus": "tagged",
                "tagPrefixList": ["${var.project_name}"],
                "countType": "imageCountMoreThan",
                "countNumber": 5
            },
            "action": {
                "type": "expire"
            }
        }
    ]
}
EOF
}
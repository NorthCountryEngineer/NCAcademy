provider "aws" {
    region = var.region
}

resource "aws_appsync_graphql_api" "api" {
    name   = "ncacademy-api"
    authentication_type = "API_KEY"

    schema {
        definition = file("${path.module}/schema.graphql")
    }
}

resource "aws_appsync_datasource" "s3_datasource" {
    api_id = aws_appsync_graphql_api.api.id
    name   = "S3DataSource"
    type   = "AMAZON_S3"

    service_role_arn = aws_iam_role.appsync.arn
}

resource "aws_iam_role" "appsync" {
    name = "appsync-s3-role"

    assume_role_policy = jsonencode({
        Version = "2012-10-17"
        Statement = [
        {
            Action = "sts:AssumeRole"
            Effect = "Allow"
            Principal = {
            Service = "appsync.amazonaws.com"
            }
        },
        ]
    })
}

provider "aws" {
    region = var.region
}

# Cognito User Pool
resource "aws_cognito_user_pool" "ncacademy_user_pool" {
    name = "ncacademy_user_pool"

    password_policy {
        minimum_length    = 8
        require_lowercase = true
        require_numbers   = true
        require_symbols   = true
        require_uppercase = true
    }
}

resource "aws_cognito_user_pool_client" "app_client" {
    name         = "appclient"
    user_pool_id = aws_cognito_user_pool.ncacademy_user_pool.id
    generate_secret = false
    explicit_auth_flows = [
        "ALLOW_USER_SRP_AUTH",
        "ALLOW_REFRESH_TOKEN_AUTH"
    ]
}

# Cognito Identity Pool
resource "aws_cognito_identity_pool" "ncacademy_identity_pool" {
    identity_pool_name               = "ncacademy_identity_pool"
    allow_unauthenticated_identities = false

    cognito_identity_providers {
        client_id               = aws_cognito_user_pool_client.app_client.id
        provider_name           = aws_cognito_user_pool.ncacademy_user_pool.endpoint
        server_side_token_check = false
    }
}

/*
# AppSync GraphQL API
resource "aws_appsync_graphql_api" "api" {
    name                = "ncacademy-api"
    authentication_type = "AMAZON_COGNITO_USER_POOLS"

    user_pool_config {
        user_pool_id = aws_cognito_user_pool.ncacademy_user_pool.id
        aws_region   = var.region
    }
}
*/

# IAM Role for Authenticated Users
resource "aws_iam_role" "auth_role" {
    name = "Cognito_ncacademy_auth_role"

    assume_role_policy = jsonencode({
        Version = "2012-10-17"
        Statement = [{
        Action = "sts:AssumeRoleWithWebIdentity",
        Effect = "Allow",
        Principal = {
            Federated = "cognito-identity.amazonaws.com"
        },
        Condition = {
            "StringEquals": {
            "cognito-identity.amazonaws.com:aud": aws_cognito_identity_pool.ncacademy_identity_pool.id
            },
            "ForAnyValue:StringLike": {
            "cognito-identity.amazonaws.com:amr": "authenticated"
            }
        }
        }]
    })
}

terraform {
    backend "s3" {
        bucket  = "ncacademy-global-tf-state"
        key     = "global_state/authentication/terraform.tfstate"
        region  = "us-east-1"
    }
}

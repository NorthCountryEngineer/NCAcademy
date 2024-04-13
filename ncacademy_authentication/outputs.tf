output "user_pool_id" {
    value       = aws_cognito_user_pool.ncacademy_user_pool.id
    description = "The ID of the Cognito User Pool"
}

/*
output "appsync_graphql_endpoint" {
    value = aws_appsync_graphql_api.api.uris["GRAPHQL"]
    description = "The endpoint URL of the AppSync GraphQL API"
}
*/

output "identity_pool_id" {
    value       = aws_cognito_identity_pool.ncacademy_identity_pool.id
    description = "The ID of the Cognito Identity Pool"
}

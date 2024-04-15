output "appsync_graphql_endpoint" {
    value = aws_appsync_graphql_api.api.uris["GRAPHQL"]
}

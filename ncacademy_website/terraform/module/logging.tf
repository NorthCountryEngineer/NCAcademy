resource "aws_cloudwatch_log_group" "app_log_group" {
    name = "${var.project_name}-${terraform.workspace}"
    tags = {
        Environment = terraform.workspace
        Project    = var.project_name
    }
}
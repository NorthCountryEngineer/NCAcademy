locals {
    env = terraform.workspace
}

resource "aws_ecs_task_definition" "ncacademy_task" {
    family                   = "${var.project_name}-${terraform.workspace}"
    network_mode             = "awsvpc"
    requires_compatibilities = ["FARGATE"]
    cpu                      = 256
    memory                   = 512
    execution_role_arn       = aws_iam_role.task_execution_role.arn

    container_definitions    = jsonencode([
        {
            name            = "${var.project_name}-${terraform.workspace}"
            image           = "${aws_ecr_repository.ncacademy_ecr_repo.repository_url}:${var.docker_image_tag}"
            cpu             = 256
            memory          = 512
            essential       = true
            environment         = [
                {
                    name            = "NODE_ENV",
                    value           = "${var.env}"
                }
            ],
            portMappings        = [
                {
                    containerPort   = 3000,
                    hostPort        = 3000
                }
            ],
            logConfiguration    = {
                logDriver           = "awslogs",
                options             = {
                    awslogs-group           = aws_cloudwatch_log_group.app_log_group.name,
                    awslogs-region          = "${var.region}",
                    awslogs-stream-prefix   = "${var.project_name}"
                }
            },
        }
    ])
}

resource "aws_ecs_service" "application_ecs" {
    name            = "${var.project_name}-ecs-${terraform.workspace}"
    cluster         = aws_ecs_cluster.application_cluster.id
    task_definition = aws_ecs_task_definition.ncacademy_task.arn
    launch_type     = "FARGATE"
    desired_count   = var.desired_count

    load_balancer {
        target_group_arn = aws_lb_target_group.application_target_group.arn
        container_name   = aws_ecs_task_definition.ncacademy_task.family
        container_port   = 3000
    }

    network_configuration {
        subnets          = [for s in aws_default_subnet.public_subnets : s.id]
        assign_public_ip = true
        security_groups  = ["${aws_security_group.application_service_security_group.id}"]
    }
}
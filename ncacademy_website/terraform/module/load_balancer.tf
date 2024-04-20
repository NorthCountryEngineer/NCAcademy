## Local variable definition
locals {
    public_subnet_ids = [for s in aws_default_subnet.public_subnets : s.id]
}

##  VPC
resource "aws_default_vpc" "default" {
    tags = {
        Name = "Using the AWS default VPC for now. todo: Segregate resources into custom VPC"
    }
}

##  ECS Cluster
resource "aws_ecs_cluster" "application_cluster" {
    name = "${var.project_name}-cluster-${var.env}"
}

##  Security Groups
resource "aws_security_group" "application_load_balancer_security_group" {
    ingress {
        from_port   = 443
        to_port     = 443
        protocol    = "tcp"
        cidr_blocks = [ "0.0.0.0/0" ]
    }
    
    ingress {
        from_port   = 80
        to_port     = 80
        protocol    = "tcp"
        cidr_blocks = [ "0.0.0.0/0" ]
    }

    egress {
        from_port   = 0
        to_port     = 0
        protocol    = "-1"
        cidr_blocks = ["0.0.0.0/0"]
    }
}

## Subnets
resource "aws_default_subnet" "public_subnets" {
    count      = length(var.public_subnet_cidrs)
    availability_zone = element(var.target_availability_zones, count.index)

    tags = {
        Name = "Public Subnet ${count.index +1}"
    }
}

##  Load Balancers
resource "aws_alb" "application_load_balancer" {
    name               = "${var.project_name}-lb-${var.env}"
    load_balancer_type = "application"
    subnets = local.public_subnet_ids
    security_groups = ["${aws_security_group.application_load_balancer_security_group.id}"]
}

    #  application target group
resource "aws_lb_target_group" "application_target_group" {
    name        = "${var.project_name}-tg-${var.env}"
    port        = 80
    protocol    = "HTTP"
    target_type = "ip"
    vpc_id      = aws_default_vpc.default.id

    health_check {
        matcher             = "200,301,302"
        path                = "/"
        interval            = 300
        timeout             = 120
        unhealthy_threshold = 5
    }
}

resource "aws_lb_listener" "application_http" {
    load_balancer_arn = aws_alb.application_load_balancer.arn
    port              = "80"
    protocol          = "HTTP"

    default_action {
        type = "forward"
        target_group_arn = aws_lb_target_group.application_target_group.arn
    }
}


resource "aws_security_group" "application_service_security_group" {
    ingress {
        from_port       = 0
        to_port         = 0
        protocol        = "-1"
        security_groups = ["${aws_security_group.application_load_balancer_security_group.id}"]
    }

    egress {
        from_port   = 0
        to_port     = 0
        protocol    = "-1"
        cidr_blocks = ["0.0.0.0/0"]
    }
}
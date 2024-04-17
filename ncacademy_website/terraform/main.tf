# Copyright (c) HashiCorp, Inc.
# SPDX-License-Identifier: MPL-2.0

provider "aws" {
  region = "us-east-1"
}

provider "cloudflare" {
  email = var.CLOUDFLARE_EMAIL
  api_key = var.CLOUDFLARE_API_KEY
}

locals {
  domain_name = terraform.workspace == "production" ? "ncacademy.app" : "ncacademy.dev"
}

## roles
resource "aws_iam_role" "ecs_execution_role" {
  name = "ecs_execution_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        },
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution_role_policy" {
  role       = aws_iam_role.ecs_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

## vpc and internet gateway
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16" 
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "ncacademy-vpc"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "Main Internet Gateway"
  }
}



## security grouping
resource "aws_security_group" "alb_sg" {
  name        = "ncacademy-alb-sg"
  description = "Security group for NC Academy ALB"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Allow HTTP inbound traffic"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1" # -1 means all protocols
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "ncacademy-alb-sg"
  }
}
## Subnets

# Main Subnet
resource "aws_subnet" "main" {
  vpc_id     = aws_vpc.main.id 
  cidr_block = "10.0.2.0/25" 

  tags = {
    Name = "Main"
  }
}

# Secondary Subnet
resource "aws_subnet" "secondary" {
  vpc_id     = aws_vpc.main.id 
  cidr_block = "10.0.2.128/25"  

  tags = {
    Name = "Secondary"
  }
}



## load balancing

resource "aws_lb" "ncacademy_alb" {
  name               = "ncacademy-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb_sg.id]
  subnets            = [aws_subnet.main.id, aws_subnet.secondary.id]

  enable_deletion_protection = false
}

resource "aws_lb_target_group" "ncacademy_tg" {
  name     = "ncacademy-tg"
  port     = 80
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id

  health_check {
    interval            = 30
    path                = "/"
    protocol            = "HTTP"
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
    matcher             = "200"
  }
}

resource "aws_lb_listener" "front_end" {
  load_balancer_arn = aws_lb.ncacademy_alb.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ncacademy_tg.arn
  }
}

## cloudfront distribution
resource "aws_cloudfront_distribution" "ncacademy_cdn" {
  origin {
    domain_name = aws_lb.ncacademy_alb.dns_name
    origin_id   = "ncacademyALB"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"

  default_cache_behavior {
    allowed_methods  = ["GET", "HEAD", "OPTIONS"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "ncacademyALB"

    forwarded_values {
      query_string = false

      cookies {
        forward = "none"
      }
    }

    viewer_protocol_policy = "redirect-to-https"
    min_ttl                = 0
    default_ttl            = 3600
    max_ttl                = 86400
  }

  price_class = "PriceClass_All"

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
}

## ECS

resource "aws_ecr_repository" "ncacademy_repo" {
  name                 = "ncacademy-repository"
  image_tag_mutability = "MUTABLE"
  
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecs_cluster" "ncacademy_cluster" {
  name = "ncacademy-cluster"
}

resource "aws_ecs_task_definition" "ncacademy_task" {
  family                   = "ncacademy-task"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  
  container_definitions = jsonencode([
    {
      name      = "ncacademy"
      image     = "${aws_ecr_repository.ncacademy_repo.repository_url}:latest"
      essential = true
      portMappings = [
        {
          containerPort = 80
          hostPort      = 80
        }
      ]
    }
  ])
}

resource "aws_ecs_service" "ncacademy_service" {
  name            = "ncacademy-service"
  cluster         = aws_ecs_cluster.ncacademy_cluster.id
  task_definition = aws_ecs_task_definition.ncacademy_task.arn
  desired_count   = 1
  
  launch_type = "FARGATE"
  
  network_configuration {
    subnets  = [aws_subnet.main.id, aws_subnet.secondary.id]
    security_groups = [aws_security_group.alb_sg.id]
    assign_public_ip = true
  }
}

## cloudflare

data "cloudflare_zones" "domain" {
  filter {
    name = local.domain_name
  }
}

data "cloudflare_record" "existing" {
  zone_id = data.cloudflare_zones.domain.zones[0].id
  hostname    = local.domain_name
  type    = "CNAME"
}

# data "cloudflare_record" "www_cname" {
#   zone_id = data.cloudflare_zones.domain.zones[0].id
#   hostname    = "www.${local.domain_name}"
#   type    = "CNAME"
# }

resource "cloudflare_record" "site_cname" {
  count = length(data.cloudflare_record.existing) > 0 ? 0 : 1
  zone_id = data.cloudflare_zones.domain.zones[0].id
  name    = local.domain_name
  value   = aws_cloudfront_distribution.ncacademy_cdn.domain_name
  type    = "CNAME"
  ttl     = 1
  proxied = true
}

output "cdn_url" {
  value = aws_cloudfront_distribution.ncacademy_cdn.domain_name
}

terraform {
  backend "s3" {
    bucket  = "ncacademy-global-tf-state"
    key     = "global_state/terraform.tfstate"
    region  = "us-east-1"
  }
}
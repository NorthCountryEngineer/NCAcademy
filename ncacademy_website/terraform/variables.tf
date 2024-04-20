# Copyright (c) HashiCorp, Inc.
# SPDX-License-Identifier: MPL-2.0

variable "AWS_REGION" {
  type        = string
  description = "The AWS region to put the bucket into"
  default     = "us-east-1"
}

variable "TF_VAR_SITE_DOMAIN" {
  type        = string
  description = "The domain name to use for the static site"
  default     = "ncacademy.dev"
}

variable "CLOUDFLARE_API_KEY" {
  type        = string
  description = "The API key to access Cloudflare"
}

variable "CLOUDFLARE_EMAIL" {
  type        = string
  description = "Emal used to access Cloudflare"
}

variable "target_availability_zones" {
  description = "availablity zones for load balancing"
  type = list(string)
  default = ["us-east-1a", "us-east-1b"]
}

variable "public_subnet_cidrs" {
  type        = list(string)
  description = "Public Subnet CIDR values"
  default     = ["10.0.1.0/24", "10.16.0.0/24"]
}

variable "route_53_domain" {
  description = "nnytech.io"
  type        = string
  default     = "nnytech.io"
}

variable "docker_image_tag" {
  type = string
}

variable "desired_count" {
    type        = number
    default     = 1
}
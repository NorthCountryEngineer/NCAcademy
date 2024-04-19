# Copyright (c) HashiCorp, Inc.
# SPDX-License-Identifier: MPL-2.0

variable "ENVIRONMENT" {
  description = "Deployment environment"
  type        = string
  default     = "development"
}

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

variable "vpc_parameters" {
  description = "VPC parameters"
  type = map(object({
    cidr_block           = string
    enable_dns_support   = optional(bool, true)
    enable_dns_hostnames = optional(bool, true)
    tags                 = optional(map(string), {})
  }))
  default = {}
}

variable "subnet_parameters" {
  description = "Subnet parameters"
  type = map(object({
    cidr_block = string
    vpc_name   = string
    tags       = optional(map(string), {})
  }))
  default = {}
}

variable "igw_parameters" {
  description = "IGW parameters"
  type = map(object({
    vpc_name = string
    tags     = optional(map(string), {})
  }))
  default = {}
}

variable "rt_parameters" {
  description = "RT parameters"
  type = map(object({
    vpc_name = string
    tags     = optional(map(string), {})
    routes = optional(list(object({
      cidr_block = string
      use_igw    = optional(bool, true)
      gateway_id = string
    })), [])
  }))
  default = {}
}

variable "rt_association_parameters" {
  description = "RT association parameters"
  type = map(object({
    subnet_name = string
    rt_name     = string
  }))
  default = {}
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
    default     = 0
}
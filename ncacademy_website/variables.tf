# Copyright (c) HashiCorp, Inc.
# SPDX-License-Identifier: MPL-2.0

variable "AWS_REGION" {
  type        = string
  description = "The AWS region to put the bucket into"
  default     = "us-east-1"
}

variable "SITE_DOMAIN" {
  type        = string
  description = "The domain name to use for the static site"
}

variable "CLOUDFLARE_API_KEY" {
  type        = string
  description = "The API key to access Cloudflare"
}
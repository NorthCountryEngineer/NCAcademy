# Copyright (c) HashiCorp, Inc.
# SPDX-License-Identifier: MPL-2.0

provider "aws" {
  region = "us-east-1"
}

provider "cloudflare" {
  api_token = var.CLOUDFLARE_API_KEY
}

resource "aws_s3_bucket" "site" {
  bucket = var.TF_VAR_SITE_DOMAIN
  force_destroy = true
}


resource "aws_s3_bucket_public_access_block" "site" {
  bucket = aws_s3_bucket.site.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_website_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  index_document {
    suffix = "index.html"
  }

  error_document {
    key = "error.html"
  }
}

resource "aws_s3_bucket_ownership_controls" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_acl" "site" {
  bucket = aws_s3_bucket.site.id

  acl = "public-read"
  depends_on = [
    aws_s3_bucket_ownership_controls.site,
    aws_s3_bucket_public_access_block.site
  ]
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadGetObject"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource = [
          aws_s3_bucket.site.arn,
          "${aws_s3_bucket.site.arn}/*",
        ]
      },
    ]
  })

  depends_on = [
    aws_s3_bucket_public_access_block.site
  ]
}

data "cloudflare_zones" "domain" {
  filter {
    name = var.TF_VAR_SITE_DOMAIN
  }
}

output "cloudflare_zones_output" {
  value = data.cloudflare_zones.domain.zones
  description = "Outputs the entire list of zones returned from Cloudflare data source"
}

resource "cloudflare_record" "site_cname" {
  zone_id = data.cloudflare_zones.domain.zones[0].id
  name    = var.TF_VAR_SITE_DOMAIN
  value   = aws_s3_bucket_website_configuration.site.website_endpoint
  type    = "CNAME"
  ttl     = 1
  proxied = false
}

resource "cloudflare_record" "www" {
  zone_id = data.cloudflare_zones.domain.zones[0].id
  name    = "www"
  value   = var.TF_VAR_SITE_DOMAIN
  type    = "CNAME"
  ttl     = 1
  proxied = true
}

resource "cloudflare_page_rule" "https" {
  zone_id = data.cloudflare_zones.domain.zones[0].id
  target  = "*.${var.TF_VAR_SITE_DOMAIN}/*"
  actions {
    always_use_https = true
  }
}

terraform {
  backend "s3" {
    bucket  = "ncacademy-global-tf-state"
    key     = "global_state/terraform.tfstate"
    region  = "us-east-1"
  }
}


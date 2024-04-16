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

resource "aws_s3_bucket" "site" {
  bucket = local.domain_name
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



resource "aws_s3_object" "index_html" {
  bucket       = aws_s3_bucket.site.id
  key          = "index.html"
  source       = "${path.module}/next/index.html"
  acl          = "public-read"
  content_type = "text/html"
}

resource "aws_s3_object" "background_png" {
  bucket       = aws_s3_bucket.site.id
  key          = "background.png"
  source       = "${path.module}/next/background.png"
  acl          = "public-read"
  content_type = "image/png"
}

resource "aws_s3_object" "site_content" {
  for_each = fileset("${path.module}/next", "**/*")
  bucket   = aws_s3_bucket.site.id
  key      = each.value
  source   = "${path.module}/next/${each.value}"
  acl      = "public-read"
  content_type = lookup({
    "html" = "text/html",
    "png"  = "image/png",
    // Add more MIME types as necessary
  }, split(".", each.value)[length(split(".", each.value)) - 1], "text/plain")
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
    name = local.domain_name
  }
}

data "cloudflare_record" "existing" {
  zone_id = data.cloudflare_zones.domain.zones[0].id
  hostname    = local.domain_name
  type    = "CNAME"
}

data "cloudflare_record" "www_cname" {
  zone_id = data.cloudflare_zones.domain.zones[0].id
  hostname    = "www.${local.domain_name}"
  type    = "CNAME"
}

resource "cloudflare_record" "site_cname" {
  count = length(data.cloudflare_record.existing) > 0 ? 0 : 1
  zone_id = data.cloudflare_zones.domain.zones[0].id
  name    = local.domain_name
  value   = "http://${aws_s3_bucket.site.id}.s3-website-us-east-1.amazonaws.com"
  type    = "CNAME"
  ttl     = 1
  proxied = true
}

resource "cloudflare_record" "www" {
  count = length(data.cloudflare_record.www_cname) > 0 ? 0 : 1
  zone_id = data.cloudflare_zones.domain.zones[0].id
  name    = "www"
  value   = "http://${aws_s3_bucket.site.id}.s3-website-us-east-1.amazonaws.com"
  type    = "CNAME"
  ttl     = 1
  proxied = true
}

output "index_page" {
  value = "http://${aws_s3_bucket.site.id}.s3-website-us-east-1.amazonaws.com"
}

output "background_image" {
  value = "http://${aws_s3_bucket.site.bucket_regional_domain_name}/background.png"
}

terraform {
  backend "s3" {
    bucket  = "ncacademy-global-tf-state"
    key     = "global_state/terraform.tfstate"
    region  = "us-east-1"
  }
}


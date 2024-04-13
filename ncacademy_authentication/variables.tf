variable "region" {
    description = "AWS region for the resources"
    default     = "us-east-1"
}

variable "TF_VAR_SITE_DOMAIN" {
    description = "The domain for the site, used in various configurations"
    default     = "ncacademy.dev"
}

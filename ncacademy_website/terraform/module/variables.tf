variable "docker_image_tag" {
    type    = string
}

variable "project_name" {
    type    = string
}

variable "env" {
    type    = string
}

variable "desired_count" {
    type    = number
}

variable "region" {
    type    = string
}

variable "public_subnet_cidrs" {
    type    = list(string)
}

variable "target_availability_zones" {
    type    = list(string)
}

variable "public_subnets" {
    type    = list(string)
}
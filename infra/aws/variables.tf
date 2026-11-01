variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "env" {
  type    = string
  default = "dev"
  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be dev or prod"
  }
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "allowed_cidrs" {
  description = "Who may reach the gateway over HTTPS (agency apps, office IPs)"
  type        = list(string)
}

variable "image" {
  description = "Container image, e.g. ghcr.io/skyops-solutions/llm-gateway:<git-sha>"
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate for the ALB listener"
  type        = string
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "desired_count" {
  type    = number
  default = 2
}

variable "max_count" {
  type    = number
  default = 6
}

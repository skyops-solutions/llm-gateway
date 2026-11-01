# Reference AWS deployment: ECS Fargate behind an ALB, ElastiCache Redis, Secrets Manager, CloudWatch.
# Validated in CI (fmt, validate, tflint, Checkov). Not applied by this repo.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
  # backend "s3" { bucket = "<state-bucket>" key = "llm-gateway/terraform.tfstate" region = "eu-central-1" use_lockfile = true }
}

provider "aws" {
  region = var.region
  default_tags { tags = { Project = "llm-gateway", Env = var.env } }
}

locals {
  name = "llm-gateway-${var.env}"
}

data "aws_availability_zones" "az" {
  # checkov:skip=CKV_AWS_394:Zones are read at plan time and only the first two are used
  state = "available"
}

# Network ---------------------------------------------------------------
resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_flow_log" "this" {
  vpc_id               = aws_vpc.this.id
  traffic_type         = "REJECT"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.flow.arn
  iam_role_arn         = aws_iam_role.flow.arn
}

resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = data.aws_availability_zones.az.names[count.index]
  tags              = { Name = "${local.name}-public-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.az.names[count.index]
  tags              = { Name = "${local.name}-private-${count.index}" }
}

resource "aws_internet_gateway" "this" { vpc_id = aws_vpc.this.id }

# The default security group is emptied so nothing can use it by accident.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id
}

resource "aws_eip" "nat" { domain = "vpc" }

resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Security groups --------------------------------------------------------
resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "HTTPS from the internet to the ALB"
  vpc_id      = aws_vpc.this.id
  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidrs
  }
  egress {
    description     = "To gateway tasks"
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
}

resource "aws_security_group" "app" {
  name        = "${local.name}-app"
  description = "Gateway tasks"
  vpc_id      = aws_vpc.this.id
  egress {
    description = "HTTPS to LLM providers"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group_rule" "app_from_alb" {
  type                     = "ingress"
  description              = "From ALB"
  security_group_id        = aws_security_group.app.id
  from_port                = 8080
  to_port                  = 8080
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.alb.id
}

resource "aws_security_group_rule" "app_to_redis" {
  type                     = "egress"
  description              = "To Redis"
  security_group_id        = aws_security_group.app.id
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.redis.id
}

resource "aws_security_group" "redis" {
  name        = "${local.name}-redis"
  description = "Redis from gateway tasks only"
  vpc_id      = aws_vpc.this.id
  ingress {
    description     = "From gateway"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
}

# Redis --------------------------------------------------------------------
resource "aws_elasticache_subnet_group" "this" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_replication_group" "this" {
  # checkov:skip=CKV_AWS_31:Access is limited to the gateway security group and TLS is on. Add an auth token before production use.
  replication_group_id       = local.name
  description                = "LLM gateway state: spend, cache, rate limits"
  engine                     = "redis"
  node_type                  = var.redis_node_type
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  subnet_group_name          = aws_elasticache_subnet_group.this.name
  security_group_ids         = [aws_security_group.redis.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                 = aws_kms_key.this.arn
  snapshot_retention_limit   = 3
}

# Secrets and encryption ----------------------------------------------------
resource "aws_kms_key" "this" {
  description         = "${local.name} secrets, logs and Redis"
  enable_key_rotation = true
  policy              = data.aws_iam_policy_document.kms.json
}

data "aws_caller_identity" "me" {}

data "aws_iam_policy_document" "kms" {
  # checkov:skip=CKV_AWS_111:In a KMS key policy, resource "*" means this key only
  # checkov:skip=CKV_AWS_109:Root statement is the AWS default key policy
  # checkov:skip=CKV_AWS_356:In a KMS key policy, resource "*" means this key only
  statement {
    sid       = "Root"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.me.account_id}:root"]
    }
  }
  statement {
    sid       = "Logs"
    actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_secretsmanager_secret" "provider_keys" {
  # checkov:skip=CKV2_AWS_57:Values are third-party provider keys that are rotated by hand, see docs/runbook.md
  name       = "${local.name}/provider-keys"
  kms_key_id = aws_kms_key.this.arn
  # Values (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, ADMIN_TOKEN) are set outside Terraform.
}

# Logs ------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name}"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.this.arn
}

resource "aws_cloudwatch_log_group" "flow" {
  name              = "/vpc/${local.name}"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.this.arn
}

resource "aws_iam_role" "flow" {
  name               = "${local.name}-flow"
  assume_role_policy = data.aws_iam_policy_document.flow_assume.json
}

data "aws_iam_policy_document" "flow_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "flow" {
  role = aws_iam_role.flow.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.flow.arn}:*"
    }]
  })
}

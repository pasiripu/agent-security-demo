terraform {
  required_version = ">= 1.7.0, < 2.0.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "agent-security-demo", Data = "synthetic", ManagedBy = "Terraform" }
  }
}

data "aws_partition" "current" {}
data "aws_ssm_parameter" "ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  name         = "agent-security-demo"
  arn          = "arn:${data.aws_partition.current.partition}"
  oidc_subject = var.oidc_subject_override != "" ? var.oidc_subject_override : "repo:${split("/", var.github_repository)[0]}@${var.github_owner_id}/${split("/", var.github_repository)[1]}@${var.github_repository_id}:environment:demo"
  oidc_arn     = var.existing_oidc_provider_arn != "" ? var.existing_oidc_provider_arn : aws_iam_openid_connect_provider.github[0].arn
  agent_config = {
    aws_region               = var.aws_region, api_key_parameter = var.api_key_parameter, github_repository = var.github_repository,
    max_daily_runs           = var.max_daily_runs, retention_days = var.retention_days,
    diagnostic_origin        = "https://diagnostics.__PUBLIC_IP__.sslip.io",
    receiver_origin          = "https://receiver.__PUBLIC_IP__.sslip.io",
    diagnostic_path_template = "/scenarios/{variant}.txt", send_run_header = true, trust_local_proxy = true,
    transport                = "public-https", data_dir = "/var/lib/agent-lab-data", enterprise_approved_origins = [],
    model                    = "claude-sonnet-4-5-20250929", claude_version = "2.1.318", max_calls = 10, url_policy = "public-web",
    timeout_seconds          = 180, max_budget_usd = var.max_budget_usd
  }
}

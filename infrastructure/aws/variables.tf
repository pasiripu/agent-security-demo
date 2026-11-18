variable "aws_region" {
  type    = string
  default = "us-west-2"
  validation {
    condition     = var.aws_region == "us-west-2"
    error_message = "This starter lab is prepared for us-west-2 (Oregon)."
  }
}
variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Supply the intended 12-digit AWS account ID."
  }
}
variable "github_repository" {
  type    = string
  default = "pasiripu/agent-security-demo"
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "Use owner/repository."
  }
}
variable "github_owner_id" { type = string }
variable "github_repository_id" { type = string }
variable "existing_oidc_provider_arn" {
  type    = string
  default = ""
}
variable "oidc_subject_override" {
  description = "Only set after checking an actual GitHub OIDC subject; exact subject, no wildcard."
  type        = string
  default     = ""
  validation {
    condition     = !strcontains(var.oidc_subject_override, "*")
    error_message = "OIDC subject must be exact."
  }
}
variable "instance_type" {
  type    = string
  default = "t3.small"
  validation {
    condition     = contains(["t3.small", "t3.medium"], var.instance_type)
    error_message = "Choose t3.small or t3.medium for this x86 lab."
  }
}
variable "ami_id" {
  type    = string
  default = ""
}
variable "api_key_parameter" {
  type    = string
  default = "/agent-security-demo/anthropic-api-key"
  validation {
    condition     = can(regex("^/[A-Za-z0-9_./-]+$", var.api_key_parameter))
    error_message = "Use a valid absolute SSM parameter name."
  }
}
variable "max_daily_runs" {
  type    = number
  default = 20
  validation {
    condition     = var.max_daily_runs >= 1 && var.max_daily_runs <= 50 && floor(var.max_daily_runs) == var.max_daily_runs
    error_message = "Daily attempts must be an integer from 1 to 50."
  }
}
variable "max_budget_usd" {
  type    = number
  default = 0.25
  validation {
    condition     = var.max_budget_usd > 0 && var.max_budget_usd <= 1
    error_message = "Per-run Claude budget must be greater than zero and at most $1."
  }
}
variable "retention_days" {
  type    = number
  default = 7
  validation {
    condition     = var.retention_days >= 1 && var.retention_days <= 30 && floor(var.retention_days) == var.retention_days
    error_message = "Retention must be 1 to 30 whole days."
  }
}
variable "auto_stop_hours" {
  type    = number
  default = 6
  validation {
    condition     = var.auto_stop_hours >= 1 && var.auto_stop_hours <= 24
    error_message = "Auto-stop must be between 1 and 24 hours."
  }
}
variable "lambda_concurrency" {
  type    = number
  default = 2
  validation {
    condition     = contains([1, 2, 3, 4, 5], var.lambda_concurrency)
    error_message = "Reserve 1 to 5 concurrent invocations per endpoint."
  }
}
variable "budget_email" {
  description = "Optional monthly AWS cost alert email; alerts do not stop spending."
  type        = string
  default     = ""
}

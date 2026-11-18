output "deployment" {
  value = {
    aws_region           = var.aws_region, aws_account_id = var.aws_account_id,
    github_repository    = var.github_repository, github_owner_id = var.github_owner_id,
    github_repository_id = var.github_repository_id,
    aws_role_arn         = aws_iam_role.github.arn, instance_id = aws_instance.agent.id,
    bucket               = aws_s3_bucket.lab.id, ssm_document = aws_ssm_document.triage.name,
    diagnostic_origin    = local.agent_config.diagnostic_origin, receiver_origin = local.agent_config.receiver_origin,
    api_key_parameter    = var.api_key_parameter, oidc_subject = local.oidc_subject,
    lambda_functions     = [aws_lambda_function.diagnostic.function_name, aws_lambda_function.receiver.function_name],
    lambda_concurrency   = var.lambda_concurrency, ami_id = aws_instance.agent.ami
  }
}

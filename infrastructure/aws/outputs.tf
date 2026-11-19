output "deployment" {
  value = {
    aws_region           = var.aws_region, aws_account_id = var.aws_account_id,
    github_repository    = var.github_repository, github_owner_id = var.github_owner_id,
    github_repository_id = var.github_repository_id,
    aws_role_arn         = aws_iam_role.github.arn, instance_id = aws_instance.agent.id,
    public_ip            = aws_instance.agent.public_ip, ssm_document = aws_ssm_document.triage.name,
    diagnostic_origin    = "https://diagnostics.${replace(aws_instance.agent.public_ip, ".", "-")}.sslip.io",
    receiver_origin      = "https://receiver.${replace(aws_instance.agent.public_ip, ".", "-")}.sslip.io",
    api_key_parameter    = var.api_key_parameter, oidc_subject = local.oidc_subject,
    source_commit        = var.source_commit, ami_id = aws_instance.agent.ami
  }
}

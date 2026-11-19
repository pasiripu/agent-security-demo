# Mock-provider lifecycle: no AWS API calls or resources. Checks rendered security properties.
mock_provider "aws" {
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/mock-role" } }
  mock_resource "aws_iam_openid_connect_provider" { defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" } }
  mock_resource "aws_ssm_document" { defaults = { arn = "arn:aws:ssm:us-west-2:123456789012:document/mock-triage" } }
  mock_resource "aws_instance" { defaults = { arn = "arn:aws:ec2:us-west-2:123456789012:instance/i-0123456789abcdef0" } }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
  mock_data "aws_ssm_parameter" { defaults = { value = "ami-0123456789abcdef0" } }
  mock_data "aws_availability_zones" { defaults = { names = ["us-west-2a"] } }
}

variables {
  aws_account_id       = "123456789012"
  github_repository    = "pasiripu/agent-security-demo"
  github_owner_id      = "68029303"
  github_repository_id = "1463082429"
  source_commit        = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}

run "offline_security_plan" {
  command = apply
  assert {
    condition     = toset([for r in aws_security_group.agent.ingress : r.from_port]) == toset([80, 443])
    error_message = "Only public HTTP/HTTPS may be inbound; no SSH or dashboard port."
  }
  assert {
    condition     = aws_instance.agent.metadata_options[0].http_tokens == "required" && aws_instance.agent.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "IMDSv2 and hop limit 1 are required."
  }
  assert {
    condition     = local.oidc_subject == "repo:pasiripu@68029303/agent-security-demo@1463082429:environment:demo"
    error_message = "Trust must use the exact new-repository immutable-ID subject."
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.github.policy, "ssm:GetParameter") && !strcontains(aws_iam_role_policy.github.policy, "AWS-RunShellScript")
    error_message = "GitHub dispatch must not read the model key or dispatch arbitrary shell documents."
  }
  assert {
    condition     = aws_instance.agent.credit_specification[0].cpu_credits == "standard"
    error_message = "Avoid unlimited-mode surplus CPU-credit charges for this demo."
  }
}

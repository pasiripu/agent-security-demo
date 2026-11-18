# Mock-provider lifecycle: no AWS API calls or resources. Checks rendered security properties.
mock_provider "aws" {
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/mock-role" } }
  mock_resource "aws_iam_openid_connect_provider" { defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" } }
  mock_resource "aws_s3_bucket" { defaults = { arn = "arn:aws:s3:::mock-synthetic-lab" } }
  mock_resource "aws_dynamodb_table" { defaults = { arn = "arn:aws:dynamodb:us-west-2:123456789012:table/mock-synthetic-lab" } }
  mock_resource "aws_ssm_document" { defaults = { arn = "arn:aws:ssm:us-west-2:123456789012:document/mock-triage" } }
  mock_resource "aws_instance" { defaults = { arn = "arn:aws:ec2:us-west-2:123456789012:instance/i-0123456789abcdef0" } }
  mock_resource "aws_lambda_function_url" { defaults = { function_url = "https://mock.lambda-url.us-west-2.on.aws/" } }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
  mock_data "aws_ssm_parameter" { defaults = { value = "ami-0123456789abcdef0" } }
  mock_data "aws_availability_zones" { defaults = { names = ["us-west-2a"] } }
}

variables {
  aws_account_id       = "123456789012"
  github_repository    = "pasiripu/agent-security-demo"
  github_owner_id      = "68029303"
  github_repository_id = "1463082429"
}

run "offline_security_plan" {
  command = apply
  assert {
    condition     = length(aws_security_group.agent.ingress) == 0
    error_message = "EC2 must have no inbound security-group rules."
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
    condition     = aws_lambda_permission.invoke["receiver"].invoked_via_function_url && aws_lambda_permission.url["receiver"].function_url_auth_type == "NONE"
    error_message = "Public receiver permissions must be limited to the function URL."
  }
}

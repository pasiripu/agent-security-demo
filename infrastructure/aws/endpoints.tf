resource "aws_iam_role" "endpoint" {
  for_each = toset(["diagnostic", "receiver"])
  name     = "${local.name}-${each.key}"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_cloudwatch_log_group" "endpoint" {
  for_each          = aws_iam_role.endpoint
  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = 7
}
resource "aws_iam_role_policy" "endpoint" {
  for_each = aws_iam_role.endpoint
  role     = each.value.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.endpoint[each.key].arn}:*" },
    { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem"], Resource = aws_dynamodb_table.runs.arn }
    ], each.key == "diagnostic" ? [
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.lab.arn}/logs/*" }
  ] : []) })
}
resource "aws_lambda_function" "receiver" {
  function_name                  = "${local.name}-receiver"
  role                           = aws_iam_role.endpoint["receiver"].arn
  runtime                        = "python3.12"
  handler                        = "handler.lambda_handler"
  filename                       = "${path.module}/.build/endpoint.zip"
  source_code_hash               = filebase64sha256("${path.module}/.build/endpoint.zip")
  timeout                        = 8
  memory_size                    = 128
  reserved_concurrent_executions = var.lambda_concurrency
  environment {
    variables = { ROLE = "receiver", RUN_TABLE = aws_dynamodb_table.runs.name, LAB_BUCKET = aws_s3_bucket.lab.id }
  }
  depends_on = [aws_iam_role_policy.endpoint]
}
resource "aws_lambda_function_url" "receiver" {
  function_name      = aws_lambda_function.receiver.function_name
  authorization_type = "NONE"
}
resource "aws_lambda_function" "diagnostic" {
  function_name                  = "${local.name}-diagnostic"
  role                           = aws_iam_role.endpoint["diagnostic"].arn
  runtime                        = "python3.12"
  handler                        = "handler.lambda_handler"
  filename                       = "${path.module}/.build/endpoint.zip"
  source_code_hash               = filebase64sha256("${path.module}/.build/endpoint.zip")
  timeout                        = 8
  memory_size                    = 128
  reserved_concurrent_executions = var.lambda_concurrency
  environment {
    variables = { ROLE = "diagnostic", RUN_TABLE = aws_dynamodb_table.runs.name, LAB_BUCKET = aws_s3_bucket.lab.id,
    RECEIVER_ORIGIN = trimsuffix(aws_lambda_function_url.receiver.function_url, "/") }
  }
  depends_on = [aws_iam_role_policy.endpoint]
}
resource "aws_lambda_function_url" "diagnostic" {
  function_name      = aws_lambda_function.diagnostic.function_name
  authorization_type = "NONE"
}
resource "aws_lambda_permission" "url" {
  for_each               = { diagnostic = aws_lambda_function.diagnostic.function_name, receiver = aws_lambda_function.receiver.function_name }
  statement_id           = "PublicSyntheticURL"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = each.value
  principal              = "*"
  function_url_auth_type = "NONE"
}
resource "aws_lambda_permission" "invoke" {
  for_each                 = { diagnostic = aws_lambda_function.diagnostic.function_name, receiver = aws_lambda_function.receiver.function_name }
  statement_id             = "PublicOnlyViaFunctionURL"
  action                   = "lambda:InvokeFunction"
  function_name            = each.value
  principal                = "*"
  invoked_via_function_url = true
}

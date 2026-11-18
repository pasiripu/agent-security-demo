resource "aws_s3_bucket" "lab" {
  bucket_prefix = "agent-security-demo-"
  force_destroy = true # Only this dedicated demo bucket; destroy removes its evidence and release ZIP.
}
resource "aws_s3_bucket_public_access_block" "lab" {
  bucket                  = aws_s3_bucket.lab.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_ownership_controls" "lab" {
  bucket = aws_s3_bucket.lab.id
  rule { object_ownership = "BucketOwnerEnforced" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "lab" {
  bucket = aws_s3_bucket.lab.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_policy" "tls" {
  bucket = aws_s3_bucket.lab.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Deny", Principal = "*", Action = "s3:*",
    Resource  = [aws_s3_bucket.lab.arn, "${aws_s3_bucket.lab.arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
}
resource "aws_s3_bucket_lifecycle_configuration" "lab" {
  bucket = aws_s3_bucket.lab.id
  dynamic "rule" {
    for_each = toset(["jobs/", "logs/", "reports/", "evidence/"])
    content {
      id     = replace(rule.value, "/", "")
      status = "Enabled"
      filter { prefix = rule.value }
      expiration { days = var.retention_days }
    }
  }
}
resource "aws_s3_object" "runtime" {
  bucket      = aws_s3_bucket.lab.id
  key         = "release/runtime.zip"
  source      = "${path.module}/.build/runtime.zip"
  source_hash = filemd5("${path.module}/.build/runtime.zip")
}
resource "aws_dynamodb_table" "runs" {
  name         = local.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_id"
  range_key    = "event_id"
  attribute {
    name = "run_id"
    type = "S"
  }
  attribute {
    name = "event_id"
    type = "S"
  }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  server_side_encryption { enabled = true }
}
resource "aws_budgets_budget" "monthly" {
  count        = var.budget_email == "" ? 0 : 1
  name         = "${local.name}-monthly-alert"
  budget_type  = "COST"
  limit_amount = "20"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }
}

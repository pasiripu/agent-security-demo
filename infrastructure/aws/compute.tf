resource "aws_vpc" "lab" {
  cidr_block           = "10.79.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}
data "aws_availability_zones" "available" { state = "available" }
resource "aws_subnet" "lab" {
  vpc_id                  = aws_vpc.lab.id
  cidr_block              = "10.79.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
}
resource "aws_internet_gateway" "lab" { vpc_id = aws_vpc.lab.id }
resource "aws_route_table" "lab" {
  vpc_id = aws_vpc.lab.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.lab.id
  }
}
resource "aws_route_table_association" "lab" {
  subnet_id      = aws_subnet.lab.id
  route_table_id = aws_route_table.lab.id
}
resource "aws_security_group" "agent" {
  name_prefix = "${local.name}-"
  description = "No inbound access; HTTPS package/model/lab calls, HTTP package mirrors"
  ingress     = []
  vpc_id      = aws_vpc.lab.id
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_iam_role" "instance" {
  name = "${local.name}-instance"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy" "ssm" {
  role = aws_iam_role.instance.id
  # SSM transport without the managed policy's wildcard parameter-read grants.
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Resource = "*", Action = [
      "ssm:DescribeAssociation", "ssm:GetDocument", "ssm:DescribeDocument", "ssm:ListAssociations",
      "ssm:ListInstanceAssociations", "ssm:PutInventory", "ssm:PutComplianceItems", "ssm:PutConfigurePackageResult",
      "ssm:UpdateAssociationStatus", "ssm:UpdateInstanceAssociationStatus", "ssm:UpdateInstanceInformation",
      "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel",
      "ec2messages:AcknowledgeMessage", "ec2messages:DeleteMessage", "ec2messages:FailMessage",
      "ec2messages:GetEndpoint", "ec2messages:GetMessages", "ec2messages:SendReply"
    ] }
  ] })
}
resource "aws_iam_role_policy" "instance" {
  role = aws_iam_role.instance.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${aws_s3_bucket.lab.arn}/release/*", "${aws_s3_bucket.lab.arn}/jobs/*"] },
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = ["${aws_s3_bucket.lab.arn}/logs/*", "${aws_s3_bucket.lab.arn}/reports/*", "${aws_s3_bucket.lab.arn}/evidence/*"] },
    { Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"], Resource = aws_dynamodb_table.runs.arn },
    { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = "${local.arn}:ssm:${var.aws_region}:${var.aws_account_id}:parameter${var.api_key_parameter}" }
  ] })
}
resource "aws_iam_instance_profile" "agent" { role = aws_iam_role.instance.name }
resource "aws_instance" "agent" {
  ami                                  = var.ami_id != "" ? var.ami_id : nonsensitive(data.aws_ssm_parameter.ami.value)
  instance_type                        = var.instance_type
  subnet_id                            = aws_subnet.lab.id
  vpc_security_group_ids               = [aws_security_group.agent.id]
  iam_instance_profile                 = aws_iam_instance_profile.agent.name
  associate_public_ip_address          = true
  instance_initiated_shutdown_behavior = "stop"
  user_data_replace_on_change          = true
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    http_protocol_ipv6          = "disabled"
    instance_metadata_tags      = "disabled"
  }
  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }
  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    region     = var.aws_region, bucket = aws_s3_bucket.lab.id, release_sha = aws_s3_object.runtime.source_hash,
    config_b64 = base64encode(jsonencode(local.agent_config)), auto_stop_hours = var.auto_stop_hours
  })
  tags       = { Name = local.name }
  depends_on = [aws_iam_role_policy.instance, aws_iam_role_policy.ssm, aws_route_table_association.lab]
}
resource "aws_ssm_document" "triage" {
  name          = "${local.name}-triage"
  document_type = "Command"
  content = jsonencode({
    schemaVersion = "2.2", description = "Only a numeric issue ID; runs installed trusted controller",
    parameters    = { IssueNumber = { type = "String", allowedPattern = "^[1-9][0-9]{0,9}$", description = "Approved GitHub issue number" } },
    mainSteps = [{ action = "aws:runShellScript", name = "Triage", inputs = {
      timeoutSeconds = "600", runCommand = ["/usr/local/bin/agent-lab-issue {{IssueNumber}}"]
    } }]
  })
}

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
  description = "Public demo HTTP/HTTPS only; administration via SSM"
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  vpc_id = aws_vpc.lab.id
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
  credit_specification {
    cpu_credits = "standard"
  }
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
    region     = var.aws_region, source_repository = var.github_repository, source_commit = var.source_commit,
    config_b64 = base64encode(jsonencode(local.agent_config)), auto_stop_hours = var.auto_stop_hours, retention_days = var.retention_days
  })
  tags       = { Name = local.name }
  depends_on = [aws_iam_role_policy.instance, aws_iam_role_policy.ssm, aws_route_table_association.lab]
}
resource "aws_ssm_document" "triage" {
  name          = "${local.name}-triage"
  document_type = "Command"
  content = jsonencode({
    schemaVersion = "2.2", description = "Only base64 JSON data; runs installed trusted demo controller",
    parameters    = { IssueB64 = { type = "String", allowedPattern = "^[A-Za-z0-9+/]+={0,2}$", description = "Approved public issue JSON encoded as base64 data" } },
    mainSteps = [{ action = "aws:runShellScript", name = "Triage", inputs = {
      timeoutSeconds = "600", runCommand = ["/usr/local/bin/agent-lab-issue '{{IssueB64}}'"]
    } }]
  })
}

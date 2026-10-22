data "aws_caller_identity" "current" {}

locals {
  safe_namespace = substr(replace(var.resource_namespace, ".", "-"), 0, 24)
  attempt_digest = substr(sha256("${var.run_id}:${var.deployment_id}:${var.region}"), 0, 24)
  role_name      = "cnb-expiry-${local.safe_namespace}-${local.attempt_digest}"
  document_name  = "cnb-expiry-${local.safe_namespace}-${local.attempt_digest}"
  script_source  = file("${path.module}/expiry.py")
}

resource "aws_iam_role" "expiry" {
  name = local.role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ssm.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:aws:ssm:${var.region}:${var.account_id}:automation-execution/*" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "expiry" {
  name = "cnb-expiry-stop-owned-instances"
  role = aws_iam_role.expiry.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ec2:StopInstances"]
        Resource = "arn:aws:ec2:${var.region}:${var.account_id}:instance/*"
        Condition = {
          StringEquals = {
            "ec2:ResourceTag/cnb_run_id"        = var.run_id
            "ec2:ResourceTag/cnb_deployment_id" = var.deployment_id
          }
        }
      }
    ]
  })
}

resource "aws_ssm_document" "expiry" {
  name            = local.document_name
  document_type   = "Automation"
  document_format = "JSON"
  content = jsonencode({
    schemaVersion = "0.3"
    description   = "Attempt-scoped compute stop fallback for ${var.run_id}"
    assumeRole    = "{{ AutomationAssumeRole }}"
    parameters = {
      AutomationAssumeRole = { type = "String" }
      ExpiryTriggerUtc     = { type = "String" }
      RunId                = { type = "String" }
      DeploymentId         = { type = "String" }
      Region               = { type = "String" }
    }
    mainSteps = [
      {
        name   = "waitUntilExpiry"
        action = "aws:sleep"
        inputs = { Timestamp = "{{ ExpiryTriggerUtc }}" }
      },
      {
        name   = "discoverAndStopOwnedInstances"
        action = "aws:executeScript"
        inputs = {
          Runtime = "python3.11"
          Handler = "handler"
          Script  = local.script_source
          InputPayload = {
            run_id        = "{{ RunId }}"
            deployment_id = "{{ DeploymentId }}"
            region        = "{{ Region }}"
          }
        }
        # Discovery runs for 300 seconds; 30 seconds remain for API response handling.
        timeoutSeconds = 330
      }
    ]
  })

  tags = {
    cnb_run_id        = var.run_id
    cnb_deployment_id = var.deployment_id
    cnb_namespace     = var.resource_namespace
    cnb_component     = "expiry"
  }
}

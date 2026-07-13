# Least-privilege execution roles. The API Lambdas and the worker Lambda get
# separate roles scoped to exactly the resources each touches.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# Shared statements ----------------------------------------------------------

locals {
  # Bedrock foundation-model ARN for the configured model, partition-aware.
  bedrock_model_arn = "arn:${local.partition}:bedrock:${local.bedrock_region}::foundation-model/${var.bedrock_model_id}"
}

data "aws_iam_policy_document" "kms_use" {
  statement {
    sid     = "UseCMK"
    effect  = "Allow"
    actions = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [local.kms_key_arn]
  }
}

# ---- API role --------------------------------------------------------------

resource "aws_iam_role" "api" {
  name               = "${local.name}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "api" {
  source_policy_documents = [data.aws_iam_policy_document.kms_use.json]

  statement {
    sid    = "Table"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]
    resources = [aws_dynamodb_table.this.arn]
  }

  statement {
    sid       = "PresignPut"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.documents.arn}/*"]
  }

  statement {
    sid       = "Enqueue"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.parse.arn]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "${local.name}-api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# ---- Worker role -----------------------------------------------------------

resource "aws_iam_role" "worker" {
  name               = "${local.name}-worker"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "worker" {
  source_policy_documents = [data.aws_iam_policy_document.kms_use.json]

  statement {
    sid    = "Table"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:BatchWriteItem",
    ]
    resources = [aws_dynamodb_table.this.arn]
  }

  statement {
    sid       = "ReadWriteDocuments"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.documents.arn, "${aws_s3_bucket.documents.arn}/*"]
  }

  statement {
    sid    = "ConsumeQueue"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.parse.arn]
  }

  statement {
    sid       = "InvokeModel"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [local.bedrock_model_arn]
  }

  dynamic "statement" {
    for_each = var.bedrock_guardrail_id != null ? [1] : []
    content {
      sid       = "ApplyGuardrail"
      effect    = "Allow"
      actions   = ["bedrock:ApplyGuardrail"]
      resources = ["arn:${local.partition}:bedrock:${local.bedrock_region}:${local.account_id}:guardrail/${var.bedrock_guardrail_id}"]
    }
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "${local.name}-worker"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

# ---- CloudWatch Logs + VPC access for both roles ---------------------------

# Logs permission scoped to this app's log groups.
data "aws_iam_policy_document" "logs" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.name}-*"]
  }
}

# ENI management for in-VPC Lambdas (private mode). AWS requires "*" resource
# for these EC2 network actions.
data "aws_iam_policy_document" "vpc" {
  statement {
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
      "ec2:AssignPrivateIpAddresses",
      "ec2:UnassignPrivateIpAddresses",
    ]
    resources = ["*"]
  }
}

locals {
  operational_policy = local.is_private ? jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      jsondecode(data.aws_iam_policy_document.logs.json).Statement,
      jsondecode(data.aws_iam_policy_document.vpc.json).Statement,
    )
  }) : data.aws_iam_policy_document.logs.json
}

resource "aws_iam_role_policy" "api_ops" {
  name   = "${local.name}-api-ops"
  role   = aws_iam_role.api.id
  policy = local.operational_policy
}

resource "aws_iam_role_policy" "worker_ops" {
  name   = "${local.name}-worker-ops"
  role   = aws_iam_role.worker.id
  policy = local.operational_policy
}

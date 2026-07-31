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
    sid       = "UseCMK"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
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
      "dynamodb:DeleteItem",
      "dynamodb:BatchWriteItem",
    ]
    resources = [aws_dynamodb_table.this.arn]
  }

  statement {
    sid    = "PresignObjects"
    effect = "Allow"
    # PutObject: presigned upload URLs. GetObject: presigned download URLs for
    # the generated Rev 5 export.
    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
    ]
    resources = ["${aws_s3_bucket.documents.arn}/*"]
  }

  statement {
    sid       = "ListObjectVersionsForPurge"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:ListBucketVersions"]
    resources = [aws_s3_bucket.documents.arn]
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
    sid    = "ReadWriteDocuments"
    effect = "Allow"
    # PutObject: the worker writes the generated Rev 5 .docx export.
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.documents.arn, "${aws_s3_bucket.documents.arn}/*"]
  }

  statement {
    sid    = "ConsumeQueue"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      # The worker re-enqueues a mapping job after a successful parse.
      "sqs:SendMessage",
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

# ---- Chat role -------------------------------------------------------------
# The chat handler is a synchronous API Lambda that reads documents/sections/
# drafts and invokes Bedrock — nothing else. It previously wore the full
# worker role, inheriting SQS consume/produce and broad S3 write/delete it
# never uses. jsonencode (not a policy-document data source) so the module
# tests can assert on the policy text under the mock provider.

resource "aws_iam_role" "chat" {
  name               = "${local.name}-chat"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy" "chat" {
  name = "${local.name}-chat"
  role = aws_iam_role.chat.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid      = "UseCMK"
          Effect   = "Allow"
          Action   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
          Resource = local.kms_key_arn
        },
        {
          Sid      = "TableRead"
          Effect   = "Allow"
          Action   = ["dynamodb:GetItem", "dynamodb:Query"]
          Resource = aws_dynamodb_table.this.arn
        },
        {
          # Oversized section bodies live under projects/*/sections/ (see
          # build_section_text_key); chat reads nothing else from the bucket.
          Sid      = "ReadSectionTexts"
          Effect   = "Allow"
          Action   = ["s3:GetObject"]
          Resource = "${aws_s3_bucket.documents.arn}/projects/*/sections/*"
        },
        {
          Sid      = "InvokeModel"
          Effect   = "Allow"
          Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
          Resource = local.bedrock_model_arn
        },
      ],
      var.bedrock_guardrail_id != null ? [
        {
          Sid      = "ApplyGuardrail"
          Effect   = "Allow"
          Action   = ["bedrock:ApplyGuardrail"]
          Resource = "arn:${local.partition}:bedrock:${local.bedrock_region}:${local.account_id}:guardrail/${var.bedrock_guardrail_id}"
        },
      ] : [],
    )
  })
}

# ---- CloudWatch Logs + VPC access for all roles ----------------------------

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

# ENI management for in-VPC Lambdas (private mode).
data "aws_iam_policy_document" "vpc" {
  # checkov:skip=CKV_AWS_111: AWS does not support resource-level permissions for
  # these EC2 network-interface actions — "*" is required for a Lambda to attach
  # to a VPC. This is the exact action set in the AWS-managed
  # AWSLambdaVPCAccessExecutionRole policy, and nothing broader.
  # checkov:skip=CKV_AWS_356: Same reason: the wildcard is on the resource, which
  # AWS mandates for these actions; the action list itself is tightly scoped.
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

resource "aws_iam_role_policy" "chat_ops" {
  name   = "${local.name}-chat-ops"
  role   = aws_iam_role.chat.id
  policy = local.operational_policy
}

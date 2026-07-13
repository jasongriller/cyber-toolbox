# Lambda functions. All handlers ship in one deployment package (var.lambda_zip_path);
# each function points at a different entrypoint. In private mode the functions run
# inside the adopter's VPC; egress-only security group, no inbound.

resource "aws_security_group" "lambda" {
  count = local.is_private ? 1 : 0

  name        = "${local.name}-lambda"
  description = "${local.name} in-VPC Lambda egress"
  vpc_id      = var.vpc_id

  egress {
    description = "All egress (reaches AWS service endpoints)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

locals {
  source_hash = filebase64sha256(var.lambda_zip_path)

  common_env = merge(
    {
      DOCUMENTS_BUCKET = aws_s3_bucket.documents.id
      TABLE_NAME       = aws_dynamodb_table.this.name
      KMS_KEY_ID       = local.kms_key_arn
      PARSE_QUEUE_URL  = aws_sqs_queue.parse.id
      BEDROCK_MODEL_ID = var.bedrock_model_id
      BEDROCK_REGION   = local.bedrock_region
    },
    var.identity_header != null ? { IDENTITY_HEADER = var.identity_header } : {},
    var.bedrock_guardrail_id != null ? {
      BEDROCK_GUARDRAIL_ID      = var.bedrock_guardrail_id
      BEDROCK_GUARDRAIL_VERSION = var.bedrock_guardrail_version
    } : {},
  )

  # name -> { handler, role? }. role defaults to "api"; "worker" grants Bedrock
  # (the chat handler invokes the model from an API request).
  api_functions = {
    create-project   = { handler = "rmf_migrator.handlers.create_project.handler" }
    request-upload   = { handler = "rmf_migrator.handlers.request_upload.handler" }
    enqueue-parse    = { handler = "rmf_migrator.handlers.enqueue_parse.handler" }
    get-job          = { handler = "rmf_migrator.handlers.get_job.handler" }
    get-document     = { handler = "rmf_migrator.handlers.review.get_document" }
    list-sections    = { handler = "rmf_migrator.handlers.review.list_sections" }
    get-mappings     = { handler = "rmf_migrator.handlers.review.get_mappings" }
    update-mapping   = { handler = "rmf_migrator.handlers.review.update_mapping" }
    approve-mappings = { handler = "rmf_migrator.handlers.review.approve_mappings" }
    get-drafts       = { handler = "rmf_migrator.handlers.drafts.get_drafts" }
    update-draft     = { handler = "rmf_migrator.handlers.drafts.update_draft" }
    approve-draft    = { handler = "rmf_migrator.handlers.drafts.approve_draft" }
    chat             = { handler = "rmf_migrator.handlers.chat.handler", role = "worker" }
    start-export      = { handler = "rmf_migrator.handlers.export.enqueue_export" }
    get-export-job    = { handler = "rmf_migrator.handlers.export.get_export_job" }
    download-export   = { handler = "rmf_migrator.handlers.export.download_export" }
    decision-log      = { handler = "rmf_migrator.handlers.export.decision_log" }
    coverage          = { handler = "rmf_migrator.handlers.coverage.coverage" }
    conversion-matrix = { handler = "rmf_migrator.handlers.coverage.conversion_matrix" }
  }

  api_role_arns = {
    api    = aws_iam_role.api.arn
    worker = aws_iam_role.worker.arn
  }
}

resource "aws_cloudwatch_log_group" "api" {
  for_each = local.api_functions

  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = local.kms_key_arn
  tags              = local.common_tags
}

resource "aws_lambda_function" "api" {
  for_each = local.api_functions

  function_name    = "${local.name}-${each.key}"
  role             = local.api_role_arns[try(each.value.role, "api")]
  runtime          = var.lambda_runtime
  handler          = each.value.handler
  filename         = var.lambda_zip_path
  source_code_hash = local.source_hash
  timeout          = 29 # aligns with API Gateway integration timeout
  memory_size      = 512

  environment {
    variables = local.common_env
  }

  dynamic "vpc_config" {
    for_each = local.is_private ? [1] : []
    content {
      subnet_ids         = var.private_subnet_ids
      security_group_ids = [aws_security_group.lambda[0].id]
    }
  }

  depends_on = [aws_cloudwatch_log_group.api]
  tags       = local.common_tags
}

# ---- Worker ----------------------------------------------------------------

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/${local.name}-parse-worker"
  retention_in_days = var.log_retention_days
  kms_key_id        = local.kms_key_arn
  tags              = local.common_tags
}

resource "aws_lambda_function" "worker" {
  function_name    = "${local.name}-parse-worker"
  role             = aws_iam_role.worker.arn
  runtime          = var.lambda_runtime
  handler          = "rmf_migrator.handlers.worker.handler"
  filename         = var.lambda_zip_path
  source_code_hash = local.source_hash
  timeout          = var.worker_timeout_seconds
  memory_size      = 1024

  environment {
    variables = local.common_env
  }

  dynamic "vpc_config" {
    for_each = local.is_private ? [1] : []
    content {
      subnet_ids         = var.private_subnet_ids
      security_group_ids = [aws_security_group.lambda[0].id]
    }
  }

  depends_on = [aws_cloudwatch_log_group.worker]
  tags       = local.common_tags
}

resource "aws_lambda_event_source_mapping" "worker" {
  event_source_arn                   = aws_sqs_queue.parse.arn
  function_name                      = aws_lambda_function.worker.arn
  batch_size                         = 1
  maximum_batching_window_in_seconds = 0
  function_response_types            = ["ReportBatchItemFailures"]
}

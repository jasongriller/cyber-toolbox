# Lambda functions. All handlers ship in one deployment package (var.lambda_zip_path);
# each function points at a different entrypoint. In private mode the functions run
# inside the adopter's VPC; egress-only security group, no inbound.

resource "aws_security_group" "lambda" {
  count = local.is_private ? 1 : 0

  name        = "${local.name}-lambda"
  description = "${local.name} in-VPC Lambda egress"
  vpc_id      = var.vpc_id

  # HTTPS only. Every service the Lambdas reach (S3, DynamoDB, SQS, Bedrock,
  # KMS, CloudWatch Logs — via VPC endpoints in private mode) is TLS on 443, so
  # there is no reason to allow all-protocol egress.
  egress {
    description = "HTTPS to AWS service endpoints"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
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
    create-project    = { handler = "rmf_migrator.handlers.create_project.handler" }
    list-projects     = { handler = "rmf_migrator.handlers.projects.list_projects" }
    list-documents    = { handler = "rmf_migrator.handlers.projects.list_documents" }
    request-upload    = { handler = "rmf_migrator.handlers.request_upload.handler" }
    enqueue-parse     = { handler = "rmf_migrator.handlers.enqueue_parse.handler" }
    get-job           = { handler = "rmf_migrator.handlers.get_job.handler" }
    get-document      = { handler = "rmf_migrator.handlers.review.get_document" }
    list-sections     = { handler = "rmf_migrator.handlers.review.list_sections" }
    get-mappings      = { handler = "rmf_migrator.handlers.review.get_mappings" }
    update-mapping    = { handler = "rmf_migrator.handlers.review.update_mapping" }
    approve-mappings  = { handler = "rmf_migrator.handlers.review.approve_mappings" }
    get-drafts        = { handler = "rmf_migrator.handlers.drafts.get_drafts" }
    update-draft      = { handler = "rmf_migrator.handlers.drafts.update_draft" }
    approve-draft     = { handler = "rmf_migrator.handlers.drafts.approve_draft" }
    chat              = { handler = "rmf_migrator.handlers.chat.handler", role = "worker" }
    start-export      = { handler = "rmf_migrator.handlers.export.enqueue_export" }
    get-export-job    = { handler = "rmf_migrator.handlers.export.get_export_job" }
    download-export   = { handler = "rmf_migrator.handlers.export.download_export" }
    decision-log      = { handler = "rmf_migrator.handlers.export.decision_log" }
    coverage          = { handler = "rmf_migrator.handlers.coverage.coverage" }
    conversion-matrix = { handler = "rmf_migrator.handlers.coverage.conversion_matrix" }
    oscal             = { handler = "rmf_migrator.handlers.coverage.oscal" }
  }

  api_role_arns = {
    api    = aws_iam_role.api.arn
    worker = aws_iam_role.worker.arn
  }
}

resource "aws_cloudwatch_log_group" "api" {
  for_each = local.api_functions

  # checkov:skip=CKV_AWS_338: Retention is the adopter's call via
  # var.log_retention_days (default 30 days). These logs carry metadata only —
  # never document content, prompts, or model responses — so a mandatory one-year
  # retention is not warranted; adopters with a records requirement raise the var.
  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = local.kms_key_arn
  tags              = local.common_tags
}

resource "aws_lambda_function" "api" {
  for_each = local.api_functions

  # checkov:skip=CKV_AWS_50: X-Ray tracing is left to the adopter; traces add a
  # service dependency and cost, and carry no benefit the CUI-safe structured
  # logs don't already provide.
  # checkov:skip=CKV_AWS_115: Reserved concurrency is intentionally unset so the
  # tool scales with the account default; adopters can cap it per their budget.
  # checkov:skip=CKV_AWS_116: A Lambda DLQ applies to asynchronous invocations.
  # These functions are invoked synchronously by API Gateway; the asynchronous
  # path (the SQS worker) has a real dead-letter queue (aws_sqs_queue.parse_dlq).
  # checkov:skip=CKV_AWS_272: Code signing requires an AWS Signer profile, which
  # would force every adopter to run a signing pipeline to deploy this tool.
  function_name    = "${local.name}-${each.key}"
  role             = local.api_role_arns[try(each.value.role, "api")]
  runtime          = var.lambda_runtime
  handler          = each.value.handler
  filename         = var.lambda_zip_path
  source_code_hash = local.source_hash
  timeout          = 29 # aligns with API Gateway integration timeout
  memory_size      = 512

  # Encrypt environment variables with the project CMK, not the AWS-managed key.
  kms_key_arn = local.kms_key_arn

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
  # checkov:skip=CKV_AWS_338: Retention is set by var.log_retention_days; these
  # logs carry metadata only (see the API log group above).
  name              = "/aws/lambda/${local.name}-parse-worker"
  retention_in_days = var.log_retention_days
  kms_key_id        = local.kms_key_arn
  tags              = local.common_tags
}

resource "aws_lambda_function" "worker" {
  # checkov:skip=CKV_AWS_50: X-Ray tracing is left to the adopter (see above).
  # checkov:skip=CKV_AWS_115: Reserved concurrency is intentionally unset.
  # checkov:skip=CKV_AWS_116: This function is driven by SQS, which already has a
  # dead-letter queue (aws_sqs_queue.parse_dlq) with maxReceiveCount = 3. A Lambda
  # DLQ would only duplicate that.
  # checkov:skip=CKV_AWS_272: Code signing would force adopters to run a signing
  # pipeline to deploy this tool.
  function_name    = "${local.name}-parse-worker"
  role             = aws_iam_role.worker.arn
  runtime          = var.lambda_runtime
  handler          = "rmf_migrator.handlers.worker.handler"
  filename         = var.lambda_zip_path
  source_code_hash = local.source_hash
  timeout          = var.worker_timeout_seconds
  memory_size      = 1024

  # Encrypt environment variables with the project CMK, not the AWS-managed key.
  kms_key_arn = local.kms_key_arn

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

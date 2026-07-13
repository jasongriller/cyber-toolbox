# Async parse/draft job queue with a dead-letter queue. CMK-encrypted.
# Visibility timeout must exceed the worker Lambda timeout so an in-flight
# message is not redelivered mid-processing.

resource "aws_sqs_queue" "parse_dlq" {
  name                      = "${local.name}-parse-dlq"
  kms_master_key_id         = local.kms_key_arn
  message_retention_seconds = 1209600 # 14 days
  tags                      = local.common_tags
}

resource "aws_sqs_queue" "parse" {
  name                       = "${local.name}-parse"
  kms_master_key_id          = local.kms_key_arn
  visibility_timeout_seconds = var.worker_timeout_seconds + 30
  message_retention_seconds  = 86400

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.parse_dlq.arn
    maxReceiveCount     = 3
  })

  tags = local.common_tags
}

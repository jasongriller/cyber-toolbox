# Operator alerting. Failure messages land in the parse DLQ only after SQS has
# exhausted its retries (maxReceiveCount = 3), so a non-empty DLQ always means
# work was lost and nobody will hear about it from the application itself. The
# alarm -> SNS path is the "nobody is watching the console" safety net.
#
# The topic always exists (so ops tooling can subscribe out-of-band); the email
# subscription is created only when alert_email is set.

resource "aws_sns_topic" "alerts" {
  name              = "${local.name}-alerts"
  kms_master_key_id = local.kms_key_arn
  tags              = local.common_tags
}

resource "aws_sns_topic_subscription" "alert_email" {
  count = var.alert_email == null ? 0 : 1

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "parse_dlq_depth" {
  alarm_name          = "${local.name}-parse-dlq-depth"
  alarm_description   = "A parse/map/draft/export job exhausted its SQS retries and dead-lettered. The document is marked failed in the UI; inspect the DLQ message and CloudWatch logs, then use the document's Retry action."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.parse_dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  # An empty DLQ emits no datapoints at all; that silence is the healthy state.
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
  ok_actions         = [aws_sns_topic.alerts.arn]
  tags               = local.common_tags
}

# ---- Scratch cleanup -----------------------------------------------------------
#
# The converter purges convert-scratch/ on every path, but the purge is
# best-effort by design: a cleanup failure must not fail a conversion that
# otherwise succeeded, so lambda_backend.py logs the exception and moves on.
# That is the right call for the job and the wrong one for the operator — an
# AccessDenied there strands a full copy of a CUI document, and nothing in the
# application would ever say so. This is the only thing that does.
#
# Scoped to the flag because the emitter is: DOC_CONVERSION_BACKEND is "reject"
# whenever enable_doc_conversion is false, and a RejectingConverter never
# stages anything to purge. The same variable creates the emitter and the
# alarm, so they cannot drift apart.

resource "aws_cloudwatch_log_metric_filter" "scratch_delete_failed" {
  count = var.enable_doc_conversion ? 1 : 0

  name = "${local.name}-scratch-delete-failed"
  # The converter backend runs inside the parse worker, so log_error writes the
  # event to the worker's log group.
  #
  # A quoted substring rather than the tighter `{ $.event = "..." }` JSON
  # selector. A JSON pattern only matches a log event that is valid JSON at the
  # top level, and whether the runtime hands CloudWatch the bare line log_error
  # wrote or wraps it in a text prefix is a property of the deployment's log
  # format, not of this code. Getting that wrong in the tight direction means an
  # alarm that never fires and nobody finds out — the exact failure this alarm
  # exists to prevent. The loose direction costs a false alarm on a log line
  # that merely quotes the event name, and nothing else in the tree emits it.
  pattern        = "\"doc_convert.scratch_delete_failed\""
  log_group_name = aws_cloudwatch_log_group.worker.name

  metric_transformation {
    name      = "ScratchDeleteFailed"
    namespace = "${local.name}/DocConvert"
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "scratch_delete_failed" {
  count = var.enable_doc_conversion ? 1 : 0

  alarm_name        = "${local.name}-scratch-delete-failed"
  alarm_description = "A converted document's scratch copies could not be purged from convert-scratch/. A full copy of the source CUI document is sitting in the documents bucket until the 1-day lifecycle rule expires it. Usual cause: the worker role is missing s3:ListBucketVersions or s3:DeleteObjectVersion."
  namespace         = aws_cloudwatch_log_metric_filter.scratch_delete_failed[0].metric_transformation[0].namespace
  metric_name       = aws_cloudwatch_log_metric_filter.scratch_delete_failed[0].metric_transformation[0].name
  statistic         = "Sum"
  period            = 300
  # One stranded copy is a finding, so no averaging window softens it.
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  # No default_value on the filter, so a healthy deployment publishes no
  # datapoints at all; that silence is the healthy state, as with the DLQ.
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
  ok_actions         = [aws_sns_topic.alerts.arn]
  tags               = local.common_tags
}

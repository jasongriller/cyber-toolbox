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

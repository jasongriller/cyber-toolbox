mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = {
      partition = "aws-us-gov"
    }
  }

  mock_data "aws_region" {
    defaults = {
      region = "us-gov-west-1"
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "test"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws-us-gov:iam::aws:role/test"
    }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws-us-gov:logs:us-gov-west-1:aws:log-group:test"
    }
  }

  mock_resource "aws_apigatewayv2_api" {
    defaults = {
      execution_arn = "arn:aws-us-gov:execute-api:us-gov-west-1:aws:test"
    }
  }

  # The alarm's alarm_actions feed off the topic ARN; the provider's ARN
  # validator runs under mock, so the default must be validly shaped.
  mock_resource "aws_sns_topic" {
    defaults = {
      arn = "arn:aws-us-gov:sns:us-gov-west-1:aws:rmf-migrator-test-alerts"
    }
  }
}

variables {
  name_prefix      = "rmf-migrator-test"
  bedrock_model_id = "mock.model-v1"
  lambda_zip_path  = "tests/lambda.fixture"
  kms_key_arn      = "arn:aws-us-gov:kms:us-gov-west-1:aws:key/test"
  network_mode     = "public"
  auth_mode        = "none" # public requires an explicit auth choice; alerting is auth-agnostic
}

# Failure messages land in the parse DLQ after SQS retries are exhausted; the
# alarm is the only signal a human gets without opening the console. command =
# apply because alarm_actions references the computed SNS topic ARN, which
# stays unknown at plan time under the mock provider.
run "dlq_alarm_notifies_the_alert_topic" {
  command = apply

  variables {
    alert_email = "ops@example.test"
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.parse_dlq_depth.namespace == "AWS/SQS" && aws_cloudwatch_metric_alarm.parse_dlq_depth.metric_name == "ApproximateNumberOfMessagesVisible"
    error_message = "The DLQ alarm must watch AWS/SQS ApproximateNumberOfMessagesVisible — the only metric that reflects messages sitting in the DLQ."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.parse_dlq_depth.dimensions["QueueName"] == aws_sqs_queue.parse_dlq.name
    error_message = "The alarm must be dimensioned on the parse DLQ's queue name, not the work queue."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.parse_dlq_depth.threshold == 1 && aws_cloudwatch_metric_alarm.parse_dlq_depth.comparison_operator == "GreaterThanOrEqualToThreshold"
    error_message = "A single dead-lettered message must trip the alarm (>= 1)."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.parse_dlq_depth.treat_missing_data == "notBreaching"
    error_message = "An empty DLQ emits no datapoints; missing data must not read as a failure."
  }

  assert {
    condition     = contains(aws_cloudwatch_metric_alarm.parse_dlq_depth.alarm_actions, aws_sns_topic.alerts.arn)
    error_message = "The alarm must publish to the module's alert topic."
  }

  assert {
    condition     = length(aws_sns_topic_subscription.alert_email) == 1 && aws_sns_topic_subscription.alert_email[0].endpoint == "ops@example.test"
    error_message = "Setting alert_email must subscribe that address to the alert topic."
  }

  assert {
    condition     = aws_sns_topic.alerts.kms_master_key_id != null && aws_sns_topic.alerts.kms_master_key_id != ""
    error_message = "The alert topic must be CMK-encrypted like every other queue/topic in the module."
  }
}

# Without an email the topic and alarm still exist (other subscribers can be
# attached out-of-band), but no subscription is created.
run "no_email_means_no_subscription" {
  command = plan

  assert {
    condition     = length(aws_sns_topic_subscription.alert_email) == 0
    error_message = "No alert_email must mean no email subscription."
  }
}

# The module-created CMK must let CloudWatch alarms publish to the encrypted
# topic; without this statement the alarm fires but SNS silently drops the
# publish (KMS AccessDenied is not surfaced anywhere useful).
run "created_kms_key_admits_cloudwatch_alarms" {
  command = plan

  variables {
    kms_key_arn = null
  }

  assert {
    condition     = strcontains(aws_kms_key.this[0].policy, "cloudwatch.amazonaws.com")
    error_message = "The CMK policy must allow cloudwatch.amazonaws.com to use the key, or alarm notifications to the CMK-encrypted SNS topic are silently dropped."
  }
}

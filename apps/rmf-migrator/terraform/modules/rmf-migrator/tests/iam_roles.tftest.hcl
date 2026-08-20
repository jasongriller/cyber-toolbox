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
  auth_mode        = "none"
  frame_ancestors  = ["http://localhost:5173"]
}

# The chat handler is a synchronous API Lambda that needs DynamoDB reads,
# section-text reads from S3, and Bedrock — nothing else. It used to wear the
# full worker role, inheriting SQS consume/produce and broad S3 write/delete
# it never uses. The chat policy is jsonencode'd (not a data source) precisely
# so its text is assertable under the mock provider, mirroring the KMS test.
# command = apply: the role/policy wiring references computed ids.
run "chat_role_grants_only_read_and_bedrock" {
  command = apply

  assert {
    condition     = strcontains(aws_iam_role_policy.chat.policy, "bedrock:InvokeModel")
    error_message = "The chat role must be able to invoke the configured Bedrock model."
  }

  assert {
    condition     = strcontains(aws_iam_role_policy.chat.policy, "dynamodb:GetItem")
    error_message = "The chat role must be able to read documents/sections/drafts from the table."
  }

  assert {
    condition     = strcontains(aws_iam_role_policy.chat.policy, "s3:GetObject")
    error_message = "The chat role must be able to read oversized section bodies from S3."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.chat.policy, "sqs:")
    error_message = "The chat role must carry no SQS permissions — chat never touches the job queue."
  }

  assert {
    condition = alltrue([
      for action in ["s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Scan"] :
      !strcontains(aws_iam_role_policy.chat.policy, action)
    ])
    error_message = "The chat role must not carry write/delete/list permissions it never uses."
  }

  assert {
    condition     = aws_iam_role_policy.chat.role == aws_iam_role.chat.id
    error_message = "The chat policy must attach to the dedicated chat role."
  }
}

# Cross-region inference profiles ("us.", "eu.", "apac.", "us-gov." prefixes)
# are not foundation models. They are account-scoped resources that fan out to
# the underlying model in several regions, so they need the profile ARN plus the
# underlying foundation model. Interpolating a profile id into a
# foundation-model ARN builds an ARN that matches nothing, and the only symptom
# is AccessDenied at invoke time, in someone else's account.
run "inference_profile_id_grants_the_profile_and_the_underlying_model" {
  command = apply

  variables {
    bedrock_model_id = "us.amazon.nova-pro-v1:0"
  }

  assert {
    condition     = strcontains(aws_iam_role_policy.chat.policy, "inference-profile/us.amazon.nova-pro-v1:0")
    error_message = "An inference-profile model id must grant invoke on the profile ARN."
  }

  assert {
    condition     = strcontains(aws_iam_role_policy.chat.policy, "foundation-model/amazon.nova-pro-v1:0")
    error_message = "The profile fans out to the underlying foundation model, which must also be granted."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.chat.policy, "foundation-model/us.amazon.nova-pro-v1:0")
    error_message = "The profile id must never be interpolated into a foundation-model ARN; that ARN matches nothing."
  }
}

# The profile path widens the foundation-model ARN to any region, because AWS
# changes which regions a profile routes to. That widening must not leak into
# deployments that name a plain model id.
run "plain_model_id_stays_pinned_to_one_region" {
  command = apply

  assert {
    condition     = strcontains(aws_iam_role_policy.chat.policy, "foundation-model/mock.model-v1")
    error_message = "A plain model id must still grant invoke on its foundation-model ARN."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.chat.policy, "inference-profile/")
    error_message = "A plain model id must not gain inference-profile permissions it cannot use."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.chat.policy, "bedrock:*::foundation-model")
    error_message = "A plain model id must stay pinned to one region, not widen to a region wildcard."
  }
}

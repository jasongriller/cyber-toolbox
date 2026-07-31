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

  # The AWS provider's ARN format validator runs even under a full mock, so
  # any resource whose computed .arn feeds another ARN-typed argument needs an
  # explicit, validly-shaped default — an auto-generated placeholder id is not
  # ARN-shaped and fails that validation.
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

# Shared across every run below; each run adds only what its scenario needs.
variables {
  name_prefix      = "rmf-migrator-test"
  bedrock_model_id = "mock.model-v1"
  lambda_zip_path  = "tests/lambda.fixture"
  # Account slot must be "aws" (or another provider-recognized token) — the
  # provider's ARN validator runs even under mock_provider and rejects an
  # arbitrary digit-free string; a 12-digit account id is banned separately by
  # this repo's own leak-prevention convention.
  kms_key_arn = "arn:aws-us-gov:kms:us-gov-west-1:aws:key/test"
}

# network_mode = "public" + auth_mode = "cognito": the posture this task adds.
# command = apply, not plan: authorizer_id on each route is a cross-resource
# reference to aws_apigatewayv2_authorizer.cognito[0].id, a computed value that
# stays unknown at plan time under the mock provider (only apply materializes
# it) — matching the same finding the stig-parser route_auth test recorded for
# its own authorizer_id reference.
run "public_cognito_requires_jwt_everywhere" {
  command = apply

  variables {
    network_mode         = "public"
    auth_mode            = "cognito"
    cognito_user_pool_id = "us-gov-west-1_MOCKPOOL"
    cognito_client_id    = "mockclientid"
  }

  assert {
    condition     = length(aws_apigatewayv2_authorizer.cognito) == 1
    error_message = "auth_mode = \"cognito\" must create exactly one JWT authorizer."
  }

  assert {
    condition     = aws_apigatewayv2_authorizer.cognito[0].authorizer_type == "JWT"
    error_message = "The Cognito authorizer must be authorizer_type = \"JWT\": HTTP API v2 (aws_apigatewayv2_authorizer) has no COGNITO_USER_POOLS type — that's REST API v1 only."
  }

  assert {
    condition     = contains(aws_apigatewayv2_authorizer.cognito[0].jwt_configuration[0].audience, "mockclientid")
    error_message = "The JWT authorizer's audience must contain the configured Cognito app client id, or a token issued to a different client would still validate."
  }

  assert {
    condition     = startswith(aws_apigatewayv2_authorizer.cognito[0].jwt_configuration[0].issuer, "https://cognito-idp.")
    error_message = "The JWT issuer must use the non-FIPS cognito-idp hostname: Cognito always stamps the non-FIPS host into a token's iss claim, regardless of which hostname issued the token."
  }

  assert {
    condition     = !strcontains(aws_apigatewayv2_authorizer.cognito[0].jwt_configuration[0].issuer, "cognito-idp-fips")
    error_message = "The JWT issuer must NOT use the FIPS hostname (cognito-idp-fips.): a real token's iss claim is always the non-FIPS host, so a FIPS issuer here would never match and would silently 401 every real login."
  }

  assert {
    condition = alltrue([
      for r in aws_apigatewayv2_route.this : r.authorization_type == "JWT"
    ])
    error_message = "Every route in local.routes must require JWT auth when auth_mode = \"cognito\"."
  }

  assert {
    condition = alltrue([
      for r in aws_apigatewayv2_route.this : r.authorizer_id != null
    ])
    error_message = "Every route must be wired to the Cognito JWT authorizer (non-null authorizer_id); a null authorizer_id here means the JWT requirement never actually runs."
  }
}

# network_mode = "private" (defaults): the pre-existing posture must be
# unchanged when auth_mode is left unset. command = plan suffices — nothing
# asserted here depends on a computed cross-resource reference (AWS_IAM is a
# literal, and zero authorizers means the count = 0 branch, never instantiated).
run "private_defaults_require_iam_and_no_authorizer" {
  command = plan

  variables {
    vpc_id             = "vpc-mock"
    private_subnet_ids = ["subnet-mock"]
    frame_ancestors    = ["https://portal.example.test"]
  }

  assert {
    condition = alltrue([
      for r in aws_apigatewayv2_route.this : r.authorization_type == "AWS_IAM"
    ])
    error_message = "network_mode = \"private\" (the default) must keep requiring AWS_IAM on every route when auth_mode is left unset."
  }

  assert {
    condition     = length(aws_apigatewayv2_authorizer.cognito) == 0
    error_message = "No JWT authorizer should exist when auth_mode resolves to \"iam\"."
  }
}

# network_mode = "public" with auth_mode left unset: proves backward
# compatibility — the existing example root sets only network_mode and must
# keep planning to the same unauthenticated posture it always has.
run "public_without_auth_mode_stays_open" {
  command = plan

  variables {
    network_mode = "public"
  }

  assert {
    condition = alltrue([
      for r in aws_apigatewayv2_route.this : r.authorization_type == "NONE"
    ])
    error_message = "network_mode = \"public\" with auth_mode unset must keep every route open (NONE) — this is the pre-existing behavior auth_mode's default derivation must reproduce."
  }

  assert {
    condition     = length(aws_apigatewayv2_authorizer.cognito) == 0
    error_message = "No JWT authorizer should exist when auth_mode resolves to \"none\"."
  }
}

# The module-created CMK must let CloudWatch Logs use it for EVERY log-group
# path the module creates. KMS denies CreateLogGroup for any group whose ARN
# is absent from the encryption-context condition, and no mock run can evaluate
# a key policy — the first live apply failed on exactly this (the condition
# listed /aws/lambda/* only, so the /aws/apigateway/* access-log group was
# denied). Pin the policy text so the next log-group consumer added without a
# matching condition entry fails here instead of mid-apply.
run "created_kms_key_covers_every_log_group_path" {
  command = plan

  variables {
    network_mode = "public"
    kms_key_arn  = null
  }

  assert {
    condition     = length(aws_kms_key.this) == 1
    error_message = "With kms_key_arn unset the module must create its own CMK."
  }

  assert {
    condition     = strcontains(aws_kms_key.this[0].policy, ":log-group:/aws/lambda/")
    error_message = "The CMK policy's CloudWatch Logs encryption-context condition must cover the Lambda log groups (/aws/lambda/<name>-*)."
  }

  assert {
    condition     = strcontains(aws_kms_key.this[0].policy, ":log-group:/aws/apigateway/")
    error_message = "The CMK policy's CloudWatch Logs encryption-context condition must cover the API Gateway access-log group (/aws/apigateway/<name>) — missing it makes CreateLogGroup fail with AccessDeniedException on a live apply."
  }
}

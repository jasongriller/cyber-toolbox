# Contract tests for the shared pool. Mock provider — no credentials, runs in CI.
mock_provider "aws" {}

variables {
  name_prefix       = "toolbox-test"
  ssm_prefix        = "/toolbox/test"
  app_client_names  = ["stig-parser-web"]
  mfa_configuration = "ON"
  tags              = {}
}

run "pool_contract" {
  command = plan

  assert {
    condition     = aws_cognito_user_pool.this.name == "toolbox-test-users"
    error_message = "Pool must be named <name_prefix>-users."
  }
  assert {
    condition     = aws_cognito_user_pool.this.admin_create_user_config[0].allow_admin_create_user_only == true
    error_message = "Self-signup must be closed: accounts are admin-created only."
  }
  assert {
    condition     = aws_cognito_user_pool.this.deletion_protection == "ACTIVE"
    error_message = "The shared pool holds every toolbox account — deletion protection stays ACTIVE."
  }
  assert {
    condition     = aws_cognito_user_pool.this.mfa_configuration == "ON"
    error_message = "MFA is ON by decision (single-factor is barred for CUI access)."
  }
  assert {
    condition     = aws_cognito_user_pool.this.software_token_mfa_configuration[0].enabled == true
    error_message = "TOTP (software token) must be the enabled MFA method."
  }
  assert {
    condition     = contains(aws_cognito_user_pool.this.username_attributes, "email")
    error_message = "Email is the username."
  }
  assert {
    condition = (
      aws_cognito_user_pool.this.password_policy[0].minimum_length == 12 &&
      aws_cognito_user_pool.this.password_policy[0].require_lowercase &&
      aws_cognito_user_pool.this.password_policy[0].require_uppercase &&
      aws_cognito_user_pool.this.password_policy[0].require_numbers &&
      aws_cognito_user_pool.this.password_policy[0].require_symbols &&
      aws_cognito_user_pool.this.password_policy[0].temporary_password_validity_days == 7
    )
    error_message = "Password policy: min 12, all four classes, 7-day temp passwords."
  }
}

run "client_contract" {
  command = plan

  assert {
    condition     = aws_cognito_user_pool_client.app["stig-parser-web"].generate_secret == false
    error_message = "SPA client must have no secret."
  }
  assert {
    condition     = toset(aws_cognito_user_pool_client.app["stig-parser-web"].explicit_auth_flows) == toset(["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"])
    error_message = "SRP + refresh only — no password/custom auth flows."
  }
  assert {
    condition     = aws_cognito_user_pool_client.app["stig-parser-web"].prevent_user_existence_errors == "ENABLED"
    error_message = "User-existence errors must be masked."
  }
  assert {
    condition     = aws_cognito_user_pool_client.app["stig-parser-web"].enable_token_revocation == true
    error_message = "Token revocation stays on."
  }
}

run "ssm_contract" {
  command = plan

  assert {
    condition     = aws_ssm_parameter.user_pool_id.name == "/toolbox/test/cognito_user_pool_id"
    error_message = "Pool-id parameter name must match the cross-app contract."
  }
  assert {
    condition     = aws_ssm_parameter.user_pool_arn.name == "/toolbox/test/cognito_user_pool_arn"
    error_message = "Pool-arn parameter name must match the cross-app contract."
  }
  assert {
    condition     = aws_ssm_parameter.client_id["stig-parser-web"].name == "/toolbox/test/cognito_client_id/stig-parser-web"
    error_message = "Client-id parameters live at <prefix>/cognito_client_id/<client-name>."
  }
}

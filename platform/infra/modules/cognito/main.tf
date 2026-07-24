# Shared toolbox user pool: one account per human across every toolbox app,
# consumed per-app through the SSM parameters below. SRP auth via
# amazon-cognito-identity-js (no hosted UI / OAuth / domain — GovCloud has no
# hosted-UI custom domains, and SDK auth is the house pattern). Cloned from the
# ssg-star cognito module, then hardened: admin-only signup, deletion
# protection, MFA. Config is written out explicitly rather than relying on
# service defaults, so a plan diff — not a console surprise — is what a future
# change looks like.
resource "aws_cognito_user_pool" "this" {
  name                = "${var.name_prefix}-users"
  user_pool_tier      = "ESSENTIALS"
  deletion_protection = "ACTIVE"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  # Accounts exist only when an operator creates them — there is no self-service
  # signup surface at all.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  mfa_configuration = var.mfa_configuration

  dynamic "software_token_mfa_configuration" {
    for_each = var.mfa_configuration == "OFF" ? [] : [1]
    content {
      enabled = true
    }
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Temp-password and recovery mail through Cognito's own sender — no SES
  # wiring exists or is needed at this volume.
  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  # Surfaces as custom:role on the token (e.g. Admin | Viewer) for future role
  # gating. Nothing reads it yet.
  schema {
    name                = "role"
    attribute_data_type = "String"
    mutable             = true
    required            = false
    string_attribute_constraints {
      min_length = 0
      max_length = 64
    }
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-users" })
}

resource "aws_cognito_user_pool_client" "app" {
  for_each = toset(var.app_client_names)

  name         = each.value
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret               = false
  explicit_auth_flows           = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  supported_identity_providers  = ["COGNITO"]
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 30
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

# Cross-stack handoff: apps read these instead of this root's state, so an app
# apply can never touch the pool and the pool can move without app edits.
resource "aws_ssm_parameter" "user_pool_id" {
  name  = "${var.ssm_prefix}/cognito_user_pool_id"
  type  = "String"
  value = aws_cognito_user_pool.this.id
  tags  = var.tags
}

resource "aws_ssm_parameter" "user_pool_arn" {
  name  = "${var.ssm_prefix}/cognito_user_pool_arn"
  type  = "String"
  value = aws_cognito_user_pool.this.arn
  tags  = var.tags
}

resource "aws_ssm_parameter" "client_id" {
  for_each = aws_cognito_user_pool_client.app

  name  = "${var.ssm_prefix}/cognito_client_id/${each.key}"
  type  = "String"
  value = each.value.id
  tags  = var.tags
}

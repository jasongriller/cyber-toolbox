# Example environment: wires the rmf-migrator module for a shared-toolbox
# deployment. Copy this directory to envs/<your-env>/, supply a real
# terraform.tfvars and backend.tf, then apply.
#
# NOTHING environment-specific belongs in this file — no account id, no
# bucket name. Everything arrives through variables.
#
# Posture: network_mode = "public" + auth_mode = "cognito", hardcoded below
# rather than exposed as variables. This app has no VPC of its own; it rides
# the toolbox's existing public API behind the shared Cognito pool (D1/D2 —
# see the phase design record) instead of the module's default
# network_mode = "private" posture, which this root deliberately does not
# offer as a choice.

terraform {
  required_version = ">= 1.7.0, < 2.0.0"

  required_providers {
    aws = {
      # The module's own versions.tf floors this at >= 6.0.0 too (fixed —
      # main.tf there reads data.aws_region.current.region, an attribute the
      # AWS provider only gained in 6.0; 5.x has just the now-deprecated
      # .name/.id, and every 5.x install fails terraform validate there).
      # Mirrored here rather than relying solely on the module's own
      # constraint; do not downgrade this to match stig's < 6.0.0 cap, which
      # is a different app on a different module.
      source  = "hashicorp/aws"
      version = ">= 6.0.0, < 7.0.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = var.tags
  }
}

# The shared pool lives in the platform root (platform/infra) and is consumed
# through its published SSM parameters — never by reading platform state.
# These fail plan-time if the platform root has not been applied yet; that
# ordering (platform first) is deliberate.
data "aws_ssm_parameter" "cognito_user_pool_id" {
  name = "${var.cognito_ssm_prefix}/cognito_user_pool_id"
}

data "aws_ssm_parameter" "cognito_client_id" {
  name = "${var.cognito_ssm_prefix}/cognito_client_id/${var.cognito_app_client_name}"
}

module "rmf_migrator" {
  source = "../../modules/rmf-migrator"

  name_prefix  = var.name_prefix
  network_mode = "public"
  auth_mode    = "cognito"

  # (D2) Both toolbox apps share ONE Cognito app client (named
  # stig-parser-web despite now serving more than that app) rather than one
  # client per app: an HTTP API v2 JWT authorizer checks a token's audience
  # against a single configured client id, so minting an rmf-only client
  # would mean a user's stig-parser login gets rejected calling the rmf API
  # (or vice versa) — breaking the single-login experience the shared pool
  # exists to provide.
  cognito_user_pool_id = nonsensitive(data.aws_ssm_parameter.cognito_user_pool_id.value)
  cognito_client_id    = nonsensitive(data.aws_ssm_parameter.cognito_client_id.value)

  # CORS allowlist for the API and for direct presigned S3 uploads. Empty
  # (the default) falls back to "*" — see variables.tf; must not ship empty
  # once this deployment has a real caller.
  frame_ancestors = var.frame_ancestors

  bedrock_model_id = var.bedrock_model_id
  lambda_zip_path  = var.lambda_zip_path

  # Unattended-failure alerting: email notified when a job dead-letters.
  alert_email = var.alert_email

  tags = var.tags
}

# Cross-stack handoff: the toolbox front door (the stig-parser env root
# today — see the phase design record for why it lives there) reads this
# instead of touching this root's state, the same pattern the platform root
# uses to publish the shared pool itself.
resource "aws_ssm_parameter" "rmf_api_url" {
  # checkov:skip=CKV2_AWS_34: The value is the public API base URL — it is
  # served to every browser client and carries no confidentiality. SecureString
  # would only add KMS coupling for the cross-stack reader.
  name  = "${var.cognito_ssm_prefix}/rmf_api_url"
  type  = "String"
  value = module.rmf_migrator.api_endpoint
  tags  = var.tags
}

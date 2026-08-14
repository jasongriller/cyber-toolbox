# Core locals, data sources, and cross-cutting validation.
#
# This module is partition-aware (aws / aws-us-gov) so the same code deploys to
# commercial AWS and GovCloud unchanged — the partition flows into every ARN we
# construct by hand.

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  partition  = data.aws_partition.current.partition
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  name = var.name_prefix

  bedrock_region = coalesce(var.bedrock_region, local.region)

  is_private = var.network_mode == "private"

  # auth_mode = null (the default) derives "iam" in private mode, preserving the
  # pre-auth_mode behavior for private callers. Public mode never derives: it
  # fails closed in validate_public_auth below, so an internet-facing API is
  # always an explicit choice. The "none" fallback here is unreachable for
  # public callers that pass validation; it exists only to keep coalesce total.
  auth_mode = coalesce(var.auth_mode, local.is_private ? "iam" : "none")

  # When the module creates its own key, use that; otherwise the caller's.
  kms_key_arn = var.kms_key_arn != null ? var.kms_key_arn : aws_kms_key.this[0].arn

  common_tags = merge(
    {
      "app"        = "rmf-rev5-migrator"
      "managed-by" = "terraform"
    },
    var.tags,
  )
}

# Cross-variable validation. Terraform's variable `validation` blocks can only see
# their own variable, so these preconditions enforce the rules that span variables.
# The conditions reference the variables directly (a constant `false` is rejected).

# A guardrail version must accompany a guardrail id.
resource "terraform_data" "validate_guardrail" {
  lifecycle {
    precondition {
      condition     = var.bedrock_guardrail_id == null || var.bedrock_guardrail_version != null
      error_message = "bedrock_guardrail_version is required when bedrock_guardrail_id is set."
    }
  }
}

# Private mode needs a VPC and subnets to place the Lambdas in.
resource "terraform_data" "validate_private_network" {
  lifecycle {
    precondition {
      condition = var.network_mode != "private" || (
        var.vpc_id != null &&
        length(var.private_subnet_ids) > 0
      )
      error_message = "network_mode = \"private\" requires vpc_id and at least one private_subnet_id. (frame_ancestors is required in every posture — see validate_cors_origins.)"
    }
  }
}

# Public mode must not silently deploy an unauthenticated API. Deriving "none"
# from a networking toggle is how a CUI deployment ends up open by accident, so
# the unauthenticated posture requires typing auth_mode = "none" yourself.
resource "terraform_data" "validate_public_auth" {
  lifecycle {
    precondition {
      condition     = var.network_mode != "public" || var.auth_mode != null
      error_message = "network_mode = \"public\" requires an explicit auth_mode: \"cognito\" (Cognito login), \"iam\" (SigV4), or \"none\" (deliberately unauthenticated — dev/demo only, never for CUI)."
    }
  }
}

# CORS must always be an explicit allowlist. An empty frame_ancestors used to
# widen the API and S3 CORS configs to ["*"]; private mode already required an
# allowlist, and public mode now does too — no posture gets wildcard CORS.
resource "terraform_data" "validate_cors_origins" {
  lifecycle {
    precondition {
      condition     = length(var.frame_ancestors) > 0
      error_message = "frame_ancestors must list at least one trusted browser origin (e.g. your portal URL, or http://localhost:5173 for a dev sandbox); CORS never falls back to \"*\"."
    }
  }
}

# Cognito mode needs a user pool and app client to build the JWT authorizer against.
resource "terraform_data" "validate_cognito_auth" {
  lifecycle {
    precondition {
      condition     = local.auth_mode != "cognito" || (var.cognito_user_pool_id != "" && var.cognito_client_id != "")
      error_message = "auth_mode = \"cognito\" requires both cognito_user_pool_id and cognito_client_id (non-empty); the env root supplies these from SSM."
    }
  }
}

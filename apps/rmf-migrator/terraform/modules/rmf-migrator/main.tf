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
        var.vpc_id != null && length(var.private_subnet_ids) > 0
      )
      error_message = "network_mode = \"private\" requires vpc_id and at least one private_subnet_id."
    }
  }
}

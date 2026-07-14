# Standalone example root — greenfield deployment.
#
# This wires the module for adopters who do NOT already have toolbox IaC. It
# defaults to network_mode = "private" — the only posture fit for CUI/GovCloud.
# A private deployment additionally needs:
#   - an existing vpc_id + private_subnet_ids
#   - VPC endpoints in that VPC for: S3 (gateway), DynamoDB (gateway), SQS,
#     Bedrock (bedrock-runtime), and CloudWatch Logs — so in-VPC Lambdas reach
#     these services without egress to the internet.
# See docs/DEPLOYMENT.md for the full private-mode checklist.
#
# network_mode = "public" stands up an UNAUTHENTICATED API (there is no
# app-level authorizer — see the module's apigateway.tf). It exists for dev and
# demo only, and must be opted into deliberately. Never put CUI through it.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
  }
}

provider "aws" {
  region = var.region
  # For GovCloud, set region to us-gov-west-1 (or us-gov-east-1). The module is
  # partition-aware and needs no other change.
}

module "rmf_migrator" {
  source = "../../modules/rmf-migrator"

  name_prefix      = var.name_prefix
  network_mode     = var.network_mode
  bedrock_model_id = var.bedrock_model_id
  lambda_zip_path  = var.lambda_zip_path

  # Private-mode inputs (ignored in public mode).
  vpc_id             = var.vpc_id
  private_subnet_ids = var.private_subnet_ids

  # Optional integration + identity.
  identity_header = var.identity_header
  frame_ancestors = var.frame_ancestors

  tags = var.tags
}

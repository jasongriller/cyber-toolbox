# Platform root: shared toolbox infrastructure. Its own backend/state — app
# applies can never touch the pool, and vice versa. Copy this directory to
# envs/<your-env>/, supply real terraform.tfvars / backend.tf / deploy.env,
# then apply via platform/deploy.sh.
#
# NOTHING environment-specific belongs in this file — no account id, no ids.
# Everything arrives through variables.

terraform {
  required_version = ">= 1.7.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.83.0, < 6.0.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = var.tags
  }
}

module "cognito" {
  source = "../../modules/cognito"

  name_prefix       = var.name_prefix
  ssm_prefix        = var.ssm_prefix
  app_client_names  = var.app_client_names
  mfa_configuration = var.mfa_configuration
  tags              = var.tags
}

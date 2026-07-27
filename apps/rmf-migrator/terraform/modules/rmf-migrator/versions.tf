terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # >= 6.0.0, not the 5.40 this previously (incorrectly) claimed: main.tf
      # reads data.aws_region.current.region, an attribute the AWS provider
      # only gained in 6.0 (5.x has just the now-deprecated .name/.id). Every
      # 5.x install fails `terraform validate` here with "Unsupported
      # attribute" — the old floor was never actually satisfiable.
      version = ">= 6.0.0, < 7.0.0"
    }
  }
}

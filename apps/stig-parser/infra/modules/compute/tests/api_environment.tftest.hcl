mock_provider "aws" {
  mock_resource "aws_lambda_layer_version" {
    defaults = {
      arn = "arn:aws-us-gov:lambda:us-gov-west-1:aws:layer:test:1"
    }
  }
}

mock_provider "archive" {
  mock_data "archive_file" {
    defaults = {
      output_path         = "/tmp/source.zip"
      output_base64sha256 = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    }
  }
}

variables {
  name_prefix          = "stig-condenser-test"
  backend_source_dir   = "."
  dependency_layer_zip = "tests/dependency-layer.fixture"

  uploads_bucket    = "test-uploads"
  artifacts_bucket  = "test-artifacts"
  job_table_name    = "test-jobs"
  state_machine_arn = "arn:aws-us-gov:states:us-gov-west-1:aws:stateMachine:test"
  role_arns = {
    api      = "arn:aws-us-gov:iam::aws:role/test-api"
    parser   = "arn:aws-us-gov:iam::aws:role/test-parser"
    enricher = "arn:aws-us-gov:iam::aws:role/test-enricher"
    exporter = "arn:aws-us-gov:iam::aws:role/test-exporter"
    marker   = "arn:aws-us-gov:iam::aws:role/test-marker"
  }

  kms_key_arn    = "arn:aws-us-gov:kms:us-gov-west-1:aws:key/test"
  bedrock_region = "us-gov-west-1"
}

run "api_environment_contract" {
  command = apply

  assert {
    condition     = length(aws_lambda_function.this["api"].vpc_config) == 0
    error_message = "The API Lambda must not have a VPC attachment; Lambdas left the VPC in this phase."
  }

  assert {
    condition = aws_lambda_function.this["api"].environment[0].variables["STATE_MACHINE_ARN"] == (
      "arn:aws-us-gov:states:us-gov-west-1:aws:stateMachine:test"
    )
    error_message = "The API Lambda must receive the state machine ARN it starts executions on."
  }

  assert {
    condition     = !contains(keys(aws_lambda_function.this["api"].environment[0].variables), "S3_PRESIGN_ENDPOINT_URL")
    error_message = "The API Lambda must no longer receive a VPC-endpoint-pinned S3 presign URL now that presigning falls back to the default public S3 endpoint."
  }
}

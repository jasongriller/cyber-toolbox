provider "aws" {
  region                      = "us-gov-west-1"
  access_key                  = "testing"
  secret_key                  = "testing"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
  skip_requesting_account_id  = true
}

override_data {
  target = data.aws_partition.current
  values = {
    partition = "aws-us-gov"
  }
}

override_data {
  target = data.aws_caller_identity.current
  values = {
    account_id = "aws"
  }
}

override_data {
  target = data.aws_region.current
  values = {
    name = "us-gov-west-1"
  }
}

variables {
  name_prefix              = "stig-condenser-test"
  uploads_bucket_arn       = "arn:aws-us-gov:s3:::test-uploads"
  artifacts_bucket_arn     = "arn:aws-us-gov:s3:::test-artifacts"
  job_table_arn            = "arn:aws-us-gov:dynamodb:us-gov-west-1:aws:table/test-jobs"
  kms_key_arn              = "arn:aws-us-gov:kms:us-gov-west-1:aws:key/test"
  state_machine_arn        = "arn:aws-us-gov:states:us-gov-west-1:aws:stateMachine:test"
  bedrock_region           = "us-gov-west-1"
  ai_killswitch_param_arn  = ""
}

run "presign_policies_scope_to_expected_actions_and_resources" {
  command = plan

  assert {
    condition = toset([
      for statement in jsondecode(data.aws_iam_policy_document.api.json).Statement : statement.Sid
      if statement.Sid == "PresignUploads" || statement.Sid == "PresignReports"
    ]) == toset(["PresignUploads", "PresignReports"])
    error_message = "Both presign policy statements (PresignUploads, PresignReports) must exist on the api role."
  }

  assert {
    condition = (
      one([
        for statement in jsondecode(data.aws_iam_policy_document.api.json).Statement : statement
        if statement.Sid == "PresignUploads"
      ]).Action == "s3:PutObject" &&
      one([
        for statement in jsondecode(data.aws_iam_policy_document.api.json).Statement : statement
        if statement.Sid == "PresignUploads"
      ]).Resource == "arn:aws-us-gov:s3:::test-uploads/jobs/*" &&
      try(one([
        for statement in jsondecode(data.aws_iam_policy_document.api.json).Statement : statement
        if statement.Sid == "PresignUploads"
      ]).Condition, null) == null
    )
    error_message = "Presigned uploads must grant only s3:PutObject on the uploads bucket's jobs/ prefix, with no network condition."
  }

  assert {
    condition = (
      one([
        for statement in jsondecode(data.aws_iam_policy_document.api.json).Statement : statement
        if statement.Sid == "PresignReports"
      ]).Action == "s3:GetObject" &&
      one([
        for statement in jsondecode(data.aws_iam_policy_document.api.json).Statement : statement
        if statement.Sid == "PresignReports"
      ]).Resource == "arn:aws-us-gov:s3:::test-artifacts/jobs/*" &&
      try(one([
        for statement in jsondecode(data.aws_iam_policy_document.api.json).Statement : statement
        if statement.Sid == "PresignReports"
      ]).Condition, null) == null
    )
    error_message = "Presigned report GET must grant only s3:GetObject on the artifacts bucket's jobs/ prefix, with no network condition."
  }
}

run "job_record_roles_allow_conditional_updates" {
  command = plan

  assert {
    condition = toset(flatten([
      one([
        for statement in jsondecode(data.aws_iam_policy_document.api.json).Statement : statement.Action
        if statement.Sid == "JobRecords"
      ])
      ])) == toset([
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ])
    error_message = "The API role must be able to create jobs and perform conditional job updates."
  }

  assert {
    condition = alltrue([
      for document in [
        data.aws_iam_policy_document.parser.json,
        data.aws_iam_policy_document.enricher.json,
        data.aws_iam_policy_document.exporter.json,
        data.aws_iam_policy_document.marker.json,
        ] : toset(flatten([
          one([
            for statement in jsondecode(document).Statement : statement.Action
            if statement.Sid == "JobRecords"
          ])
          ])) == toset([
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
      ])
    ])
    error_message = "Stage roles must use scoped GetItem and conditional UpdateItem permissions without PutItem."
  }
}

run "api_can_reconcile_only_its_workflow_executions" {
  command = plan

  assert {
    condition = (
      toset(flatten([
        one([
          for statement in jsondecode(data.aws_iam_policy_document.api.json).Statement : statement.Action
          if statement.Sid == "CancelPipeline"
        ])
        ])) == toset([
        "states:DescribeExecution",
        "states:StopExecution",
      ]) &&
      one([
        for statement in jsondecode(data.aws_iam_policy_document.api.json).Statement : statement.Resource
        if statement.Sid == "CancelPipeline"
      ]) == "arn:aws-us-gov:states:us-gov-west-1:aws:execution:test:*"
    )
    error_message = "Launch recovery and cancellation must be limited to executions of this state machine."
  }
}

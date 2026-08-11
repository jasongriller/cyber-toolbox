mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = {
      partition = "aws-us-gov"
    }
  }

  mock_data "aws_region" {
    defaults = {
      region = "us-gov-west-1"
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "test"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws-us-gov:iam::aws:role/test"
    }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws-us-gov:logs:us-gov-west-1:aws:log-group:test"
    }
  }

  mock_resource "aws_apigatewayv2_api" {
    defaults = {
      execution_arn = "arn:aws-us-gov:execute-api:us-gov-west-1:aws:test"
    }
  }

  mock_resource "aws_sns_topic" {
    defaults = {
      arn = "arn:aws-us-gov:sns:us-gov-west-1:aws:rmf-migrator-test-alerts"
    }
  }

  # The converter Lambda's image_uri is built from this. Deliberately not a
  # 12-digit account id: CI's leak scan greps tracked terraform files for one.
  mock_resource "aws_ecr_repository" {
    defaults = {
      repository_url = "test.dkr.ecr.us-gov-west-1.amazonaws.com/rmf-migrator-test-doc-converter"
    }
  }

  # The two reads the converter security group resolves its egress destinations
  # from. Both are given plausible values rather than generated ones, because
  # the egress assertions are about what those destinations are.
  #
  # cidr_block is the VPC's primary range only. A VPC may carry secondary CIDR
  # associations, and an adopter's KMS or CloudWatch Logs interface endpoint can
  # sit in a subnet carved from one — so the mock has two associations and the
  # egress assertion reads the association list, not the primary.
  mock_data "aws_vpc" {
    defaults = {
      cidr_block = "10.0.0.0/16"
      cidr_block_associations = [
        {
          association_id = "vpc-cidr-assoc-mockprimary"
          cidr_block     = "10.0.0.0/16"
          state          = "associated"
        },
        {
          association_id = "vpc-cidr-assoc-mocksecondary"
          cidr_block     = "10.90.0.0/16"
          state          = "associated"
        },
      ]
    }
  }

  mock_data "aws_ec2_managed_prefix_list" {
    defaults = {
      id = "pl-mocks3"
    }
  }
}

variables {
  name_prefix      = "rmf-migrator-test"
  bedrock_model_id = "mock.model-v1"
  lambda_zip_path  = "tests/lambda.fixture"
  kms_key_arn      = "arn:aws-us-gov:kms:us-gov-west-1:aws:key/test"
  network_mode     = "public"
  auth_mode        = "none"
  frame_ancestors  = ["http://localhost:5173"]
}

# ---- Flag off (the default) --------------------------------------------------

# LibreOffice lands inside the accreditation boundary, so the whole feature is
# opt-in. "Opt-in" has to mean no resource and no widened grant, not just an
# application-level refusal.
run "disabled_by_default_deploys_no_converter" {
  command = plan

  assert {
    condition = alltrue([
      length(aws_ecr_repository.converter) == 0,
      length(aws_iam_role.converter) == 0,
      length(aws_iam_role_policy.converter) == 0,
      length(aws_lambda_function.converter) == 0,
      length(aws_security_group.converter) == 0,
      length(aws_cloudwatch_log_group.converter) == 0,
    ])
    error_message = "With enable_doc_conversion off, no converter resource may be planned."
  }

  assert {
    condition = alltrue([
      length(aws_cloudwatch_log_metric_filter.scratch_delete_failed) == 0,
      length(aws_cloudwatch_metric_alarm.scratch_delete_failed) == 0,
    ])
    error_message = "The scratch-purge alarm exists only alongside the converter that can emit the event."
  }

  assert {
    condition = alltrue([
      aws_lambda_function.worker.environment[0].variables["DOC_CONVERSION_BACKEND"] == "reject",
      aws_lambda_function.api["request-upload"].environment[0].variables["DOC_CONVERSION_BACKEND"] == "reject",
    ])
    error_message = "Both the worker and the upload API must read DOC_CONVERSION_BACKEND=reject when the flag is off."
  }

  assert {
    condition     = aws_lambda_function.worker.environment[0].variables["DOC_CONVERTER_FUNCTION_NAME"] == ""
    error_message = "No converter function exists to name when the flag is off; config.from_env reads empty as None."
  }

  assert {
    condition = alltrue([
      for action in ["s3:DeleteObjectVersion", "s3:ListBucketVersions", "lambda:InvokeFunction"] :
      !contains(flatten([for s in data.aws_iam_policy_document.worker.statement : s.actions]), action)
    ])
    error_message = "The worker must not carry version-delete or converter-invoke rights it cannot use with the flag off."
  }

  assert {
    condition     = output.converter_security_group_id == null
    error_message = "With enable_doc_conversion off there is no converter security group to reach; the output must be null, not an empty string or an error."
  }
}

# Resource 6 of the plan, and the mitigation that survives the other two being
# misconfigured. convert-scratch/ holds whole CUI documents; delete_project
# purges only projects/<id>/, so nothing else in the tree ever expires them.
# It must therefore live outside the feature flag, and inside the one existing
# lifecycle configuration — a second aws_s3_bucket_lifecycle_configuration on
# the same bucket is a whole-config replace, so the two would clobber each
# other on alternate applies.
run "scratch_lifecycle_rule_is_not_flag_scoped" {
  command = plan

  assert {
    condition = alltrue([
      for id in ["expire-convert-scratch", "expire-noncurrent-versions"] :
      contains([for r in aws_s3_bucket_lifecycle_configuration.documents.rule : r.id], id)
    ])
    error_message = "Both lifecycle rules must live in aws_s3_bucket_lifecycle_configuration.documents, with the flag off."
  }

  assert {
    condition = anytrue([
      for r in aws_s3_bucket_lifecycle_configuration.documents.rule :
      r.status == "Enabled" && r.filter[0].prefix == "convert-scratch/" && r.expiration[0].days == 1
      if r.id == "expire-convert-scratch"
    ])
    error_message = "The scratch rule must expire CURRENT objects under convert-scratch/ after 1 day, not just noncurrent versions."
  }
}

# ---- Flag on -----------------------------------------------------------------

# Every bound here is load-bearing, not tuning. The timeout has to stay under
# CONVERT_CONFIG's 150s read timeout or botocore abandons a conversion that is
# still running and will still write to scratch keys the caller has purged; the
# concurrency cap is what stops an upload flood from draining the account pool
# a synchronous API shares.
run "enabled_converter_is_bounded_and_in_a_vpc" {
  command = apply

  variables {
    enable_doc_conversion              = true
    converter_image_tag                = "mock-build-0001"
    vpc_id                             = "vpc-mockisolated"
    converter_subnet_ids               = ["subnet-mockisolatedone", "subnet-mockisolatedtwo"]
    converter_accepts_whole_vpc_egress = true
  }

  assert {
    condition     = aws_lambda_function.converter[0].timeout == var.converter_timeout_seconds && var.converter_timeout_seconds < 150
    error_message = "The converter timeout must come from var.converter_timeout_seconds and stay below the 150s CONVERT_CONFIG read timeout."
  }

  assert {
    condition     = aws_lambda_function.converter[0].reserved_concurrent_executions == var.converter_reserved_concurrency
    error_message = "The converter must carry a reserved-concurrency cap; each in-flight job holds a blocked worker and a 2 GB converter at once."
  }

  assert {
    condition     = aws_lambda_function.converter[0].package_type == "Image" && aws_lambda_function.converter[0].memory_size == 2048
    error_message = "The converter ships as the container image built by converter.Dockerfile, at 2048 MB."
  }

  assert {
    condition = alltrue([
      for subnet in var.converter_subnet_ids :
      contains(aws_lambda_function.converter[0].vpc_config[0].subnet_ids, subnet)
    ])
    error_message = "The converter must run in the supplied egress-free subnets; a Lambda outside a VPC has full internet egress, and LibreOffice uses it."
  }

  assert {
    condition     = output.converter_security_group_id == aws_security_group.converter[0].id
    error_message = "converter_security_group_id must expose the converter's own security group so an adopter can wire reciprocal ingress on the endpoint security groups named in converter_endpoint_security_group_ids."
  }

  assert {
    condition = alltrue([
      aws_lambda_function.converter[0].environment[0].variables["DOCUMENTS_BUCKET"] == aws_s3_bucket.documents.id,
      aws_lambda_function.converter[0].environment[0].variables["KMS_KEY_ID"] == local.kms_key_arn,
    ])
    error_message = "The handler reads both from the environment and refuses an event naming any other bucket; without them every invocation raises KeyError."
  }

  assert {
    condition = alltrue([
      aws_lambda_function.worker.environment[0].variables["DOC_CONVERSION_BACKEND"] == "lambda",
      aws_lambda_function.worker.environment[0].variables["DOC_CONVERTER_FUNCTION_NAME"] == aws_lambda_function.converter[0].function_name,
    ])
    error_message = "The worker must be pointed at the converter it is allowed to invoke."
  }

  assert {
    condition     = aws_cloudwatch_log_group.converter[0].retention_in_days == var.log_retention_days
    error_message = "The converter log group must follow the module's retention convention."
  }

  assert {
    condition     = aws_ecr_repository.converter[0].image_scanning_configuration[0].scan_on_push
    error_message = "The converter image carries a full LibreOffice; it must be scanned on push."
  }

  assert {
    condition     = aws_ecr_repository.converter[0].image_tag_mutability == "IMMUTABLE" && endswith(aws_lambda_function.converter[0].image_uri, ":${var.converter_image_tag}")
    error_message = "The function must run the named image build by tag; IMMUTABLE blocks overwriting an existing tag, though not a delete-then-repush of it — see var.converter_image_digest for a pin that survives that."
  }

  # The way out has to work as reliably as the way in. By the time the flag can
  # be turned off the repository necessarily holds an image, and ECR refuses to
  # delete a non-empty repository — so without this, setting
  # enable_doc_conversion = false errors the apply partway through and leaves
  # the operator unable to tell a failed disable from a completed one.
  assert {
    condition     = aws_ecr_repository.converter[0].force_delete
    error_message = "aws_ecr_repository.converter must set force_delete; otherwise turning enable_doc_conversion back off fails the apply on RepositoryNotEmptyException."
  }
}

# IMMUTABLE tag mutability blocks overwriting an EXISTING tag; it does not
# block a delete-then-repush of the same tag (ecr:BatchDeleteImage +
# ecr:PutImage), which leaves image_uri's literal "<repo>:<tag>" form with no
# plan diff to show for it. converter_image_digest is the pin that survives
# that: named, it must win over the tag form.
run "enabled_converter_image_digest_pins_by_content_over_the_tag" {
  command = plan

  variables {
    enable_doc_conversion              = true
    converter_image_tag                = "mock-build-0001"
    converter_image_digest             = "sha256:${join("", [for i in range(64) : "a"])}"
    vpc_id                             = "vpc-mockisolated"
    converter_subnet_ids               = ["subnet-mockisolatedone"]
    converter_accepts_whole_vpc_egress = true
  }

  assert {
    condition     = aws_lambda_function.converter[0].image_uri == "${aws_ecr_repository.converter[0].repository_url}@${var.converter_image_digest}"
    error_message = "With converter_image_digest set, the function must be pinned by content digest, not by the repushable tag."
  }
}

# The default path: no digest supplied, so the function still runs by tag —
# the only option before the first image has ever been pushed.
run "enabled_converter_with_no_digest_still_runs_by_tag" {
  command = plan

  variables {
    enable_doc_conversion              = true
    converter_image_tag                = "mock-build-0001"
    vpc_id                             = "vpc-mockisolated"
    converter_subnet_ids               = ["subnet-mockisolatedone"]
    converter_accepts_whole_vpc_egress = true
  }

  assert {
    condition     = aws_lambda_function.converter[0].image_uri == "${aws_ecr_repository.converter[0].repository_url}:${var.converter_image_tag}"
    error_message = "With no converter_image_digest, the function must still be addressable by tag."
  }
}

# The converter is the one place untrusted OLE2 bytes are parsed. Its role is
# the containment boundary: scratch keys and the CMK, nothing else.
run "enabled_converter_role_reaches_only_scratch" {
  command = plan

  variables {
    enable_doc_conversion              = true
    converter_image_tag                = "mock-build-0001"
    vpc_id                             = "vpc-mockisolated"
    converter_subnet_ids               = ["subnet-mockisolatedone"]
    converter_accepts_whole_vpc_egress = true
  }

  # mock_data "aws_iam_policy_document" gives every policy document in the module
  # the same json, so an equality assertion against converter_ops.json would hold
  # no matter which document the role_policy actually named. This gives the
  # module-wide logs document — the one attaching it here would be the bug — a
  # json of its own, so the comparison below can tell them apart. Deliberately
  # only this one: overriding converter_ops itself would replace its statement
  # list with generated values and hollow out the two assertions after it.
  override_data {
    target = data.aws_iam_policy_document.logs
    values = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"ModuleWideLogs\"}]}"
    }
  }

  assert {
    condition = setunion(
      toset(flatten([for s in data.aws_iam_policy_document.converter[0].statement : s.actions])),
      toset(["s3:GetObject", "s3:PutObject", "kms:Decrypt", "kms:GenerateDataKey"]),
    ) == toset(["s3:GetObject", "s3:PutObject", "kms:Decrypt", "kms:GenerateDataKey"])
    error_message = "The converter role must grant only scratch object access and CMK use — no DynamoDB, no Bedrock, no SQS."
  }

  assert {
    condition = alltrue([
      for action in ["s3:GetObject", "s3:PutObject", "kms:Decrypt", "kms:GenerateDataKey"] :
      contains(flatten([for s in data.aws_iam_policy_document.converter[0].statement : s.actions]), action)
    ])
    error_message = "The converter still has to read the staged .doc, write the .docx, and use the CMK for both."
  }

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.converter[0].statement :
      alltrue([for r in s.resources : endswith(r, "/convert-scratch/*")])
      if anytrue([for a in s.actions : startswith(a, "s3:")])
    ])
    error_message = "Every S3 grant on the converter role must be scoped to the convert-scratch/ prefix."
  }

  # The role's other policy, and the one where a widening is least visible.
  # data.aws_iam_policy_document.logs scopes to /aws/lambda/${local.name}-*,
  # which is every function this module creates, and an IAM wildcard matches ":"
  # as readily as any other character — so attaching it here would hand a
  # compromised converter logs:PutLogEvents on the parse worker's log streams.
  # That is the log group aws_cloudwatch_log_metric_filter.scratch_delete_failed
  # reads, so the converter could forge the event and fire — or exhaust — the
  # one alarm that reports stranded CUI.
  assert {
    condition     = aws_iam_role_policy.converter_ops[0].policy == data.aws_iam_policy_document.converter_ops[0].json
    error_message = "converter_ops must be exactly data.aws_iam_policy_document.converter_ops; the module-wide logs document reaches every function's log group."
  }

  assert {
    condition = setunion(
      toset(flatten([for s in data.aws_iam_policy_document.converter_ops[0].statement : s.actions])),
      toset([
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "ec2:CreateNetworkInterface",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DeleteNetworkInterface",
        "ec2:AssignPrivateIpAddresses",
        "ec2:UnassignPrivateIpAddresses",
      ]),
      ) == toset([
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "ec2:CreateNetworkInterface",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DeleteNetworkInterface",
        "ec2:AssignPrivateIpAddresses",
        "ec2:UnassignPrivateIpAddresses",
    ])
    error_message = "converter_ops may grant only its own log stream writes and the ENI actions a VPC Lambda needs — not logs:CreateLogGroup, which aws_cloudwatch_log_group.converter already covers."
  }

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.converter_ops[0].statement :
      alltrue([
        for r in s.resources :
        r == "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.name}-doc-converter:*"
      ])
      if anytrue([for a in s.actions : startswith(a, "logs:")])
    ])
    error_message = "The converter's logs grant must name its own log group only; /aws/lambda/${local.name}-* covers the parse worker's, whose stream the scratch-purge alarm watches."
  }
}

# The security group is the half of the egress control that stays in force after
# apply. The route-table enumeration is a plan-time read: anyone holding
# ec2:CreateRoute can add 0.0.0.0/0 -> igw- to the converter subnet's table and
# Terraform does not notice until the next plan. Naming the destinations here
# means that drift buys nothing.
run "enabled_converter_egress_names_no_open_internet" {
  command = apply

  variables {
    enable_doc_conversion              = true
    converter_image_tag                = "mock-build-0001"
    vpc_id                             = "vpc-mockisolated"
    converter_subnet_ids               = ["subnet-mockisolatedone"]
    converter_accepts_whole_vpc_egress = true
  }

  assert {
    condition = alltrue([
      for rule in aws_security_group.converter[0].egress :
      !contains(rule.cidr_blocks, "0.0.0.0/0") && !try(contains(rule.ipv6_cidr_blocks, "::/0"), false)
    ])
    error_message = "No converter egress rule may name the open internet; a later route-table change would then be all it takes to give LibreOffice a way out."
  }

  # Every associated range, not just the primary: an endpoint ENI in a subnet
  # carved from a secondary association would otherwise be unreachable, and an
  # unreachable KMS endpoint hangs every conversion to the function timeout
  # while an unreachable Logs endpoint takes the diagnostics with it.
  assert {
    condition = anytrue([
      for rule in aws_security_group.converter[0].egress :
      toset(rule.cidr_blocks) == toset([for a in data.aws_vpc.converter[0].cidr_block_associations : a.cidr_block]) &&
      contains(rule.prefix_list_ids, data.aws_ec2_managed_prefix_list.s3[0].id)
    ])
    error_message = "The converter still has to reach the in-VPC KMS and CloudWatch Logs interface endpoints — in every one of the VPC's associated IPv4 ranges — and the S3 gateway endpoint's managed prefix list."
  }
}

# Whole-VPC reach on 443 is a real lateral-movement primitive: var.vpc_id is
# consumed rather than created, so the converter shares that network with
# whatever else the adopter runs in it, and code execution in soffice.bin is the
# premise the entire isolation design is built on. Naming the endpoints' own
# security groups is what removes it, and the CIDR rule must then be gone rather
# than merely joined.
run "endpoint_security_groups_replace_the_whole_vpc_egress" {
  command = apply

  variables {
    enable_doc_conversion                 = true
    converter_image_tag                   = "mock-build-0001"
    vpc_id                                = "vpc-mockisolated"
    converter_subnet_ids                  = ["subnet-mockisolatedone"]
    converter_endpoint_security_group_ids = ["sg-mockkmsendpoint", "sg-mocklogsendpoint"]
  }

  assert {
    condition = alltrue([
      for rule in aws_security_group.converter[0].egress :
      length(coalesce(rule.cidr_blocks, [])) == 0
    ])
    error_message = "With the endpoint security groups named, no converter egress rule may still name a CIDR range; the VPC-wide destination is what this variable exists to remove."
  }

  assert {
    condition = anytrue([
      for rule in aws_security_group.converter[0].egress :
      toset(rule.security_groups) == toset(var.converter_endpoint_security_group_ids) &&
      contains(rule.prefix_list_ids, data.aws_ec2_managed_prefix_list.s3[0].id)
    ])
    error_message = "The converter must still reach the named KMS and CloudWatch Logs endpoints and the S3 gateway endpoint's managed prefix list."
  }
}

# Gate 2. delete_prefix lists object versions and deletes them by VersionId;
# ReadWriteDocuments grants neither, so without these the purge AccessDenies on
# every conversion and a full CUI copy is stranded outside every purge path.
# The prefix scoping is the security-relevant half: the worker is the highest-
# exposure component here, and bucket-wide version delete would let a foothold
# there permanently erase every project's documents and exports.
run "enabled_worker_can_purge_scratch_versions" {
  command = plan

  variables {
    enable_doc_conversion              = true
    converter_image_tag                = "mock-build-0001"
    vpc_id                             = "vpc-mockisolated"
    converter_subnet_ids               = ["subnet-mockisolatedone"]
    converter_accepts_whole_vpc_egress = true
  }

  assert {
    condition = alltrue([
      for action in ["s3:DeleteObjectVersion", "s3:ListBucketVersions", "lambda:InvokeFunction"] :
      contains(flatten([for s in data.aws_iam_policy_document.worker.statement : s.actions]), action)
    ])
    error_message = "The worker needs version list+delete for delete_prefix and invoke rights on the converter."
  }

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.worker.statement :
      alltrue([for r in s.resources : endswith(r, "/convert-scratch/*")])
      if contains(s.actions, "s3:DeleteObjectVersion")
    ])
    error_message = "s3:DeleteObjectVersion must stay scoped to convert-scratch/; bucket-wide it would let a compromised worker erase every version of every document."
  }

  assert {
    condition = anytrue([
      for s in data.aws_iam_policy_document.worker.statement :
      setunion(s.actions, ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]) == toset(["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"])
      if s.sid == "ReadWriteDocuments"
    ])
    error_message = "ReadWriteDocuments is bucket-wide and must be left exactly as it was; the new rights belong in their own prefix-scoped statements."
  }

  assert {
    condition = anytrue([
      for s in data.aws_iam_policy_document.worker.statement :
      contains(s.resources, aws_s3_bucket.documents.arn) && length(s.condition) > 0
      if contains(s.actions, "s3:ListBucketVersions")
    ])
    error_message = "s3:ListBucketVersions is a bucket-level action, so it must carry an s3:prefix condition rather than a bare bucket ARN."
  }
}

# The purge is best-effort by design — a cleanup failure must not fail a job
# that otherwise succeeded — so the only thing standing between an AccessDenied
# and an unnoticed pile of CUI copies is this alarm.
run "enabled_alarms_on_a_failed_scratch_purge" {
  command = apply

  variables {
    enable_doc_conversion              = true
    converter_image_tag                = "mock-build-0001"
    vpc_id                             = "vpc-mockisolated"
    converter_subnet_ids               = ["subnet-mockisolatedone"]
    converter_accepts_whole_vpc_egress = true
  }

  assert {
    condition     = aws_cloudwatch_log_metric_filter.scratch_delete_failed[0].log_group_name == aws_cloudwatch_log_group.worker.name
    error_message = "The converter backend runs inside the worker, so the event lands in the worker's log group."
  }

  assert {
    condition     = strcontains(aws_cloudwatch_log_metric_filter.scratch_delete_failed[0].pattern, "doc_convert.scratch_delete_failed")
    error_message = "The metric filter must match the event name log_error emits."
  }

  assert {
    condition = alltrue([
      aws_cloudwatch_metric_alarm.scratch_delete_failed[0].namespace == aws_cloudwatch_log_metric_filter.scratch_delete_failed[0].metric_transformation[0].namespace,
      aws_cloudwatch_metric_alarm.scratch_delete_failed[0].metric_name == aws_cloudwatch_log_metric_filter.scratch_delete_failed[0].metric_transformation[0].name,
    ])
    error_message = "The alarm must watch the metric the filter publishes."
  }

  assert {
    condition = alltrue([
      aws_cloudwatch_metric_alarm.scratch_delete_failed[0].threshold == 1,
      aws_cloudwatch_metric_alarm.scratch_delete_failed[0].comparison_operator == "GreaterThanOrEqualToThreshold",
      aws_cloudwatch_metric_alarm.scratch_delete_failed[0].treat_missing_data == "notBreaching",
    ])
    error_message = "One stranded scratch copy must trip the alarm, and the silence of a healthy deployment must not."
  }

  assert {
    condition     = contains(aws_cloudwatch_metric_alarm.scratch_delete_failed[0].alarm_actions, aws_sns_topic.alerts.arn)
    error_message = "The alarm must publish to the module's alert topic."
  }
}

# ---- Fail-closed checks ------------------------------------------------------

# _CONVERTER_FUNCTION_TIMEOUT in backend/tests/test_doc_convert.py asserts the
# client stays above 120; this refuses the other direction. Changing one side
# alone now breaks the other.
run "converter_timeout_may_not_reach_the_client_read_timeout" {
  command = plan

  variables {
    enable_doc_conversion              = true
    converter_image_tag                = "mock-build-0001"
    vpc_id                             = "vpc-mockisolated"
    converter_subnet_ids               = ["subnet-mockisolatedone"]
    converter_timeout_seconds          = 150
    converter_accepts_whole_vpc_egress = true
  }

  expect_failures = [var.converter_timeout_seconds]
}

# -1 is the AWS sentinel for "no reservation" and a legal value for the
# provider's reserved_concurrent_executions, so an operator who hits throttles
# can lift the cap by copying it out of the AWS docs — and the variable's own
# description still promises a bound that is no longer there.
run "converter_reserved_concurrency_may_not_be_unreserved" {
  command = plan

  variables {
    enable_doc_conversion              = true
    converter_image_tag                = "mock-build-0001"
    vpc_id                             = "vpc-mockisolated"
    converter_subnet_ids               = ["subnet-mockisolatedone"]
    converter_reserved_concurrency     = -1
    converter_accepts_whole_vpc_egress = true
  }

  expect_failures = [var.converter_reserved_concurrency]
}

# The gate this closes: converter_endpoint_security_group_ids defaults to [],
# and left that way the security group's fallback egress rule (converter.tf)
# targets every CIDR associated with var.vpc_id — reach into the whole of an
# existing, adopter-owned network from the one process assumed reachable by a
# hostile document. Neither the endpoint security groups nor the explicit
# opt-out are supplied here, so the plan must fail rather than silently take
# the loose form.
run "enabling_conversion_without_endpoint_sgs_or_opt_out_fails_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  expect_failures = [terraform_data.validate_converter_network]
}

# Lambda kills the worker at worker_timeout_seconds mid-invoke; the converter
# can run up to converter_timeout_seconds (default 120) and the client read
# timeout is 150. A worker cut off before either finishes never reaches the
# finally: block that purges scratch, and Lambda does not cancel the
# invocation on client disconnect — so the converter keeps running and still
# writes its output, stranding both the source .doc and the converted .docx
# under convert-scratch/ with no scratch_delete_failed line ever emitted to
# trip the alarm. worker_timeout_seconds must clear the 150s read timeout, not
# merely converter_timeout_seconds, whenever the feature is on.
run "enabling_conversion_requires_worker_timeout_above_the_client_read_timeout" {
  command = plan

  variables {
    enable_doc_conversion              = true
    converter_image_tag                = "mock-build-0001"
    vpc_id                             = "vpc-mockisolated"
    converter_subnet_ids               = ["subnet-mockisolatedone"]
    converter_accepts_whole_vpc_egress = true
    worker_timeout_seconds             = 60
  }

  expect_failures = [terraform_data.validate_converter_network]
}

# Turning the flag on without egress-free subnets would deploy a converter with
# full internet access, which is the configuration Gate 3 measured LibreOffice
# using. Fail the plan instead.
run "enabling_conversion_requires_converter_subnets" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
  }

  expect_failures = [terraform_data.validate_converter_network]
}

# An IMMUTABLE repository rejects a re-pushed tag, so a defaulted "latest"
# would silently pin whatever was pushed first and quietly reject every rebuild
# after it. Naming the build is the whole point.
run "enabling_conversion_requires_an_image_tag" {
  command = plan

  variables {
    enable_doc_conversion              = true
    vpc_id                             = "vpc-mockisolated"
    converter_subnet_ids               = ["subnet-mockisolatedone"]
    converter_accepts_whole_vpc_egress = true
  }

  expect_failures = [terraform_data.validate_converter_image]
}

# Supplying subnets is not the same as supplying egress-free ones, and the
# difference is exactly what Gate 3 is about — so the route tables are read,
# not trusted. The mock provider returns an empty route list, so without the
# runs below the route precondition would pass on every run without ever being
# exercised. There is one run per next-hop field the check enumerates, and every
# field the check reads — the next hops plus destination_prefix_list_id, which
# is what separates a gateway endpoint from a Gateway Load Balancer one — is
# spelled out in each override, because the ones an override leaves out are
# filled with generated values rather than blanks, which would make all of these
# pass for the wrong reason.
run "converter_subnets_routing_to_a_nat_gateway_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        destination_prefix_list_id = ""
        gateway_id                 = ""
        vpc_endpoint_id            = ""
        nat_gateway_id             = "nat-mockegress"
        transit_gateway_id         = ""
        egress_only_gateway_id     = ""
        carrier_gateway_id         = ""
        instance_id                = ""
        network_interface_id       = ""
        vpc_peering_connection_id  = ""
        local_gateway_id           = ""
        core_network_arn           = ""
        odb_network_arn            = ""
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

run "converter_subnets_routing_to_an_internet_gateway_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        destination_prefix_list_id = ""
        gateway_id                 = "igw-mockegress"
        vpc_endpoint_id            = ""
        nat_gateway_id             = ""
        transit_gateway_id         = ""
        egress_only_gateway_id     = ""
        carrier_gateway_id         = ""
        instance_id                = ""
        network_interface_id       = ""
        vpc_peering_connection_id  = ""
        local_gateway_id           = ""
        core_network_arn           = ""
        odb_network_arn            = ""
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

run "converter_subnets_routing_to_a_transit_gateway_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        destination_prefix_list_id = ""
        gateway_id                 = ""
        vpc_endpoint_id            = ""
        nat_gateway_id             = ""
        transit_gateway_id         = "tgw-mockegress"
        egress_only_gateway_id     = ""
        carrier_gateway_id         = ""
        instance_id                = ""
        network_interface_id       = ""
        vpc_peering_connection_id  = ""
        local_gateway_id           = ""
        core_network_arn           = ""
        odb_network_arn            = ""
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

run "converter_subnets_routing_to_an_egress_only_gateway_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        destination_prefix_list_id = ""
        gateway_id                 = ""
        vpc_endpoint_id            = ""
        nat_gateway_id             = ""
        transit_gateway_id         = ""
        egress_only_gateway_id     = "eigw-mockegress"
        carrier_gateway_id         = ""
        instance_id                = ""
        network_interface_id       = ""
        vpc_peering_connection_id  = ""
        local_gateway_id           = ""
        core_network_arn           = ""
        odb_network_arn            = ""
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

run "converter_subnets_routing_to_a_carrier_gateway_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        destination_prefix_list_id = ""
        gateway_id                 = ""
        vpc_endpoint_id            = ""
        nat_gateway_id             = ""
        transit_gateway_id         = ""
        egress_only_gateway_id     = ""
        carrier_gateway_id         = "cagw-mockegress"
        instance_id                = ""
        network_interface_id       = ""
        vpc_peering_connection_id  = ""
        local_gateway_id           = ""
        core_network_arn           = ""
        odb_network_arn            = ""
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

# A virtual private gateway is the reason gateway_id is read as an allow list.
# It is egress — to the on-premises network across a VPN or Direct Connect —
# and it is not an internet gateway, so a check that rejects only "igw-" lets
# it through.
run "converter_subnets_routing_to_a_virtual_private_gateway_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        destination_prefix_list_id = ""
        gateway_id                 = "vgw-mockegress"
        vpc_endpoint_id            = ""
        nat_gateway_id             = ""
        transit_gateway_id         = ""
        egress_only_gateway_id     = ""
        carrier_gateway_id         = ""
        instance_id                = ""
        network_interface_id       = ""
        vpc_peering_connection_id  = ""
        local_gateway_id           = ""
        core_network_arn           = ""
        odb_network_arn            = ""
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

# A NAT *instance* is the pre-NAT-gateway pattern and gives exactly the same
# internet access as nat_gateway_id. The route names it as an instance...
run "converter_subnets_routing_to_a_nat_instance_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        destination_prefix_list_id = ""
        gateway_id                 = ""
        vpc_endpoint_id            = ""
        nat_gateway_id             = ""
        transit_gateway_id         = ""
        egress_only_gateway_id     = ""
        carrier_gateway_id         = ""
        instance_id                = "i-mocknatinstance"
        network_interface_id       = ""
        vpc_peering_connection_id  = ""
        local_gateway_id           = ""
        core_network_arn           = ""
        odb_network_arn            = ""
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

# ...or, for the same appliance, as the ENI it forwards through.
run "converter_subnets_routing_to_a_network_interface_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        destination_prefix_list_id = ""
        gateway_id                 = ""
        vpc_endpoint_id            = ""
        nat_gateway_id             = ""
        transit_gateway_id         = ""
        egress_only_gateway_id     = ""
        carrier_gateway_id         = ""
        instance_id                = ""
        network_interface_id       = "eni-mocknatinstance"
        vpc_peering_connection_id  = ""
        local_gateway_id           = ""
        core_network_arn           = ""
        odb_network_arn            = ""
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

# A peering connection reaches another VPC, which may itself hold the NAT
# gateway this subnet was built without.
run "converter_subnets_routing_to_a_peering_connection_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        destination_prefix_list_id = ""
        gateway_id                 = ""
        vpc_endpoint_id            = ""
        nat_gateway_id             = ""
        transit_gateway_id         = ""
        egress_only_gateway_id     = ""
        carrier_gateway_id         = ""
        instance_id                = ""
        network_interface_id       = ""
        vpc_peering_connection_id  = "pcx-mockpeering"
        local_gateway_id           = ""
        core_network_arn           = ""
        odb_network_arn            = ""
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

# An Outposts local gateway leaves the VPC for the on-premises network.
run "converter_subnets_routing_to_a_local_gateway_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        destination_prefix_list_id = ""
        gateway_id                 = ""
        vpc_endpoint_id            = ""
        nat_gateway_id             = ""
        transit_gateway_id         = ""
        egress_only_gateway_id     = ""
        carrier_gateway_id         = ""
        instance_id                = ""
        network_interface_id       = ""
        vpc_peering_connection_id  = ""
        local_gateway_id           = "lgw-mockoutpost"
        core_network_arn           = ""
        odb_network_arn            = ""
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

# Cloud WAN is a transit gateway that does not name itself one.
run "converter_subnets_routing_to_a_core_network_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        destination_prefix_list_id = ""
        gateway_id                 = ""
        vpc_endpoint_id            = ""
        nat_gateway_id             = ""
        transit_gateway_id         = ""
        egress_only_gateway_id     = ""
        carrier_gateway_id         = ""
        instance_id                = ""
        network_interface_id       = ""
        vpc_peering_connection_id  = ""
        local_gateway_id           = ""
        core_network_arn           = "arn:aws-us-gov:networkmanager::aws:core-network/core-network-mock"
        odb_network_arn            = ""
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

# The last next-hop field the provider exposes, and the one most likely to be
# overlooked precisely because nothing here uses Oracle Database@AWS.
run "converter_subnets_routing_to_an_odb_network_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        destination_prefix_list_id = ""
        gateway_id                 = ""
        vpc_endpoint_id            = ""
        nat_gateway_id             = ""
        transit_gateway_id         = ""
        egress_only_gateway_id     = ""
        carrier_gateway_id         = ""
        instance_id                = ""
        network_interface_id       = ""
        vpc_peering_connection_id  = ""
        local_gateway_id           = ""
        core_network_arn           = ""
        odb_network_arn            = "arn:aws-us-gov:odb:us-gov-west-1:aws:odb-network/odbnet-mock"
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

# The check has to discriminate, not just reject. A gateway VPC endpoint is a
# route with a gateway_id like any other — and it is the route the converter
# actually needs to reach S3. These two values are the whole allow list, so
# this is also what stops the inverted gateway_id test from rejecting every
# usable configuration. AWS installs a gateway-endpoint route with a prefix-list
# destination, which is also what tells it apart from the Gateway Load Balancer
# endpoint below.
run "gateway_endpoint_routes_are_not_treated_as_egress" {
  command = plan

  variables {
    enable_doc_conversion              = true
    converter_image_tag                = "mock-build-0001"
    vpc_id                             = "vpc-mockisolated"
    converter_subnet_ids               = ["subnet-mockisolatedone"]
    converter_accepts_whole_vpc_egress = true
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [
        {
          destination_prefix_list_id = "pl-mocks3"
          gateway_id                 = "vpce-mocks3"
          vpc_endpoint_id            = ""
          nat_gateway_id             = ""
          transit_gateway_id         = ""
          egress_only_gateway_id     = ""
          carrier_gateway_id         = ""
          instance_id                = ""
          network_interface_id       = ""
          vpc_peering_connection_id  = ""
          local_gateway_id           = ""
          core_network_arn           = ""
          odb_network_arn            = ""
        },
        {
          destination_prefix_list_id = ""
          gateway_id                 = "local"
          vpc_endpoint_id            = ""
          nat_gateway_id             = ""
          transit_gateway_id         = ""
          egress_only_gateway_id     = ""
          carrier_gateway_id         = ""
          instance_id                = ""
          network_interface_id       = ""
          vpc_peering_connection_id  = ""
          local_gateway_id           = ""
          core_network_arn           = ""
          odb_network_arn            = ""
        },
      ]
    }
  }

  assert {
    condition     = length(local.converter_egress_routes) == 0
    error_message = "A gateway VPC endpoint route must not read as internet egress; rejecting it would reject the only working configuration."
  }
}

# The other thing a "vpce-" target can be. A Gateway Load Balancer endpoint is
# how traffic is handed to an inspection appliance, and the appliance forwards
# it onward — so a default route pointing at one is full egress wearing the
# prefix the allow list above is built around. What separates the two is the
# destination: AWS installs a gateway-endpoint route as a prefix list, while a
# GWLB endpoint is the target of a CIDR route.
run "converter_subnets_routing_to_a_gwlb_endpoint_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        cidr_block                 = "0.0.0.0/0"
        destination_prefix_list_id = ""
        gateway_id                 = ""
        vpc_endpoint_id            = "vpce-mockgwlb"
        nat_gateway_id             = ""
        transit_gateway_id         = ""
        egress_only_gateway_id     = ""
        carrier_gateway_id         = ""
        instance_id                = ""
        network_interface_id       = ""
        vpc_peering_connection_id  = ""
        local_gateway_id           = ""
        core_network_arn           = ""
        odb_network_arn            = ""
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

# The same route, landing in the field the provider uses when it does not split
# a "vpce-" target out of the GatewayId the API returns. Which of the two fields
# holds it is a provider-version detail, so neither may admit it without a
# prefix-list destination.
run "converter_subnets_routing_to_a_gwlb_endpoint_by_gateway_id_fail_closed" {
  command = plan

  variables {
    enable_doc_conversion = true
    converter_image_tag   = "mock-build-0001"
    vpc_id                = "vpc-mockisolated"
    converter_subnet_ids  = ["subnet-mockisolatedone"]
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        cidr_block                 = "0.0.0.0/0"
        destination_prefix_list_id = ""
        gateway_id                 = "vpce-mockgwlb"
        vpc_endpoint_id            = ""
        nat_gateway_id             = ""
        transit_gateway_id         = ""
        egress_only_gateway_id     = ""
        carrier_gateway_id         = ""
        instance_id                = ""
        network_interface_id       = ""
        vpc_peering_connection_id  = ""
        local_gateway_id           = ""
        core_network_arn           = ""
        odb_network_arn            = ""
      }]
    }
  }

  expect_failures = [terraform_data.validate_converter_network]
}

# And the mirror of the two above: an S3 gateway endpoint reported in
# vpc_endpoint_id rather than gateway_id is still the route the converter needs.
# Rejecting a "vpce-" target outright would reject it.
run "gateway_endpoint_routes_in_the_vpc_endpoint_field_are_not_treated_as_egress" {
  command = plan

  variables {
    enable_doc_conversion              = true
    converter_image_tag                = "mock-build-0001"
    vpc_id                             = "vpc-mockisolated"
    converter_subnet_ids               = ["subnet-mockisolatedone"]
    converter_accepts_whole_vpc_egress = true
  }

  override_data {
    target = data.aws_route_table.converter[0]
    values = {
      routes = [{
        destination_prefix_list_id = "pl-mocks3"
        gateway_id                 = ""
        vpc_endpoint_id            = "vpce-mocks3"
        nat_gateway_id             = ""
        transit_gateway_id         = ""
        egress_only_gateway_id     = ""
        carrier_gateway_id         = ""
        instance_id                = ""
        network_interface_id       = ""
        vpc_peering_connection_id  = ""
        local_gateway_id           = ""
        core_network_arn           = ""
        odb_network_arn            = ""
      }]
    }
  }

  assert {
    condition     = length(local.converter_egress_routes) == 0
    error_message = "A gateway VPC endpoint route must not read as internet egress, whichever field the provider reports its target in."
  }
}

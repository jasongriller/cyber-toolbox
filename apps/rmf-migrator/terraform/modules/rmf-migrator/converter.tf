# Legacy binary .doc conversion, entirely behind var.enable_doc_conversion.
#
# LibreOffice parses a macro-bearing format with a long CVE history, so it gets
# its own container-image Lambda, its own role, and its own subnets — never a
# seat inside the API or parse workers. The role below is the containment
# boundary the converter handler asserts against: scratch keys and the CMK,
# nothing else.
#
# Two things here are corrections of earlier drafts rather than defaults.
# A Lambda *outside* a VPC has full internet egress, and this build uses it —
# a .doc naming a linked graphic made it issue OPTIONS/HEAD/GET during a
# conversion that then succeeded. So the function is always in a VPC, and every
# next-hop field of every route in its subnets' route tables is enumerated below
# and required to name a gateway VPC endpoint on a prefix-list destination, or
# nothing. And the timeout is a variable, not a literal, because it has to stay
# under the invoking client's read timeout; see var.converter_timeout_seconds.

resource "aws_ecr_repository" "converter" {
  # checkov:skip=CKV_AWS_136: Encrypted with the AWS-managed ECR key rather than
  # this module's CMK. The image is LibreOffice plus the handler — no CUI ever
  # lands in it — and pointing ECR at the CMK would require opening a
  # kms:CreateGrant path on a key policy that deliberately admits only
  # CloudWatch Logs and CloudWatch alarms.
  count = var.enable_doc_conversion ? 1 : 0

  name = "${local.name}-doc-converter"

  # Immutable: this blocks overwriting an EXISTING tag, so an ordinary rebuild
  # has to land under a new tag and shows up as a plan diff. It does not block
  # a delete-then-repush of the same tag (ecr:BatchDeleteImage + ecr:PutImage,
  # unremarkable now that force_delete below makes image deletion routine) —
  # that swap produces no plan diff for var.converter_image_tag's literal
  # "<repo>:<tag>" image_uri. var.converter_image_digest is the pin that
  # survives it.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  # The flag has to be reversible. converter_image_tag has no default, so by the
  # time anyone can set enable_doc_conversion = false the repository holds at
  # least one image, and ECR rejects the delete of a non-empty repository — the
  # apply would error partway through, leaving the operator with no signal
  # separating "the disable worked and only the repo cleanup failed" from "the
  # disable did not take". Nothing here is worth keeping across that: the image
  # is LibreOffice plus the handler, and no CUI ever lands in it.
  force_delete = true

  encryption_configuration {
    encryption_type = "KMS"
  }

  tags = local.common_tags
}

# The tag has no default precisely so this fires. See var.converter_image_tag.
resource "terraform_data" "validate_converter_image" {
  count = var.enable_doc_conversion ? 1 : 0

  lifecycle {
    precondition {
      condition     = var.converter_image_tag != null && var.converter_image_tag != ""
      error_message = "enable_doc_conversion = true requires converter_image_tag: name the converter image build you mean to run. The repository rejects a re-pushed tag, so an implied \"latest\" would pin the first image ever pushed."
    }
  }
}

resource "aws_cloudwatch_log_group" "converter" {
  # checkov:skip=CKV_AWS_338: Retention is set by var.log_retention_days, as for
  # every other function in this module. These logs carry key names and error
  # types — never document content.
  count = var.enable_doc_conversion ? 1 : 0

  name              = "/aws/lambda/${local.name}-doc-converter"
  retention_in_days = var.log_retention_days
  kms_key_id        = local.kms_key_arn
  tags              = local.common_tags
}

# ---- Role ---------------------------------------------------------------------

resource "aws_iam_role" "converter" {
  count = var.enable_doc_conversion ? 1 : 0

  name               = "${local.name}-doc-converter"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "converter" {
  count = var.enable_doc_conversion ? 1 : 0

  # Deliberately not source_policy_documents = [kms_use.json]: that grants
  # kms:DescribeKey too, and this function only decrypts what it reads and
  # generates a data key for what it writes.
  statement {
    sid       = "UseCMK"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [local.kms_key_arn]
  }

  # The staged .doc in, the converted .docx out. The handler additionally
  # refuses any event whose keys fall outside CONVERT_SCRATCH_PREFIX; this is
  # the boundary that check is asserting was not widened by accident.
  statement {
    sid       = "ReadWriteConvertScratch"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.documents.arn}/convert-scratch/*"]
  }
}

resource "aws_iam_role_policy" "converter" {
  count = var.enable_doc_conversion ? 1 : 0

  name   = "${local.name}-doc-converter"
  role   = aws_iam_role.converter[0].id
  policy = data.aws_iam_policy_document.converter[0].json
}

# Logs and ENI management. Unlike the other roles this is neither
# local.operational_policy nor data.aws_iam_policy_document.logs, and both
# departures are deliberate. operational_policy carries the VPC statements only
# in private mode, and the converter is in a VPC in every mode. The logs
# document scopes to /aws/lambda/${local.name}-*, which is every function this
# module creates — and an IAM wildcard matches ":" like any other character, so
# that resource covers /aws/lambda/${local.name}-parse-worker:log-stream:...
# too. This is the one role assumed to be reachable by a hostile .doc, and the
# parse worker's log group is where the scratch_delete_failed metric filter
# reads: a converter that could write there could forge that event at will and
# either bury the operator in phantom alarms or get the only control reporting
# stranded CUI muted. So it gets its own statement, on its own group.
data "aws_iam_policy_document" "converter_ops" {
  count = var.enable_doc_conversion ? 1 : 0

  # No logs:CreateLogGroup: aws_cloudwatch_log_group.converter creates the group
  # with the module's retention and CMK, and a function that can create its own
  # would silently replace a deleted one with an unencrypted, never-expiring
  # group instead of failing loudly.
  statement {
    sid    = "WriteOwnLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.name}-doc-converter:*"]
  }

  # checkov:skip=CKV_AWS_111: AWS does not support resource-level permissions for
  # these EC2 network-interface actions — "*" is required for a Lambda to attach
  # to a VPC. This is the exact action set in the AWS-managed
  # AWSLambdaVPCAccessExecutionRole policy, and nothing broader.
  # checkov:skip=CKV_AWS_356: Same reason: the wildcard is on the resource, which
  # AWS mandates for these actions; the action list itself is tightly scoped.
  statement {
    sid    = "ManageOwnNetworkInterfaces"
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
      "ec2:AssignPrivateIpAddresses",
      "ec2:UnassignPrivateIpAddresses",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "converter_ops" {
  count = var.enable_doc_conversion ? 1 : 0

  name   = "${local.name}-doc-converter-ops"
  role   = aws_iam_role.converter[0].id
  policy = data.aws_iam_policy_document.converter_ops[0].json
}

# ---- Network ------------------------------------------------------------------

# The two destinations the converter legitimately reaches, resolved rather than
# hardcoded. Everything it needs is either inside the VPC (the KMS and
# CloudWatch Logs interface endpoints, which are ENIs holding VPC addresses) or
# behind the S3 gateway endpoint, whose route AWS installs as an AWS-managed
# prefix list.
data "aws_vpc" "converter" {
  count = var.enable_doc_conversion ? 1 : 0

  id = var.vpc_id
}

data "aws_ec2_managed_prefix_list" "s3" {
  count = var.enable_doc_conversion ? 1 : 0

  name = "com.amazonaws.${local.region}.s3"
}

resource "aws_security_group" "converter" {
  count = var.enable_doc_conversion ? 1 : 0

  name        = "${local.name}-doc-converter"
  description = "${local.name} converter Lambda egress to VPC endpoints only"
  vpc_id      = var.vpc_id

  # HTTPS only, and only to the S3 prefix list plus one of two destinations for
  # the interface endpoints — their own security groups when the adopter names
  # them, the VPC's associated address ranges otherwise.
  # This is the half of the egress control that stays in force after apply: the
  # route-table enumeration below is a plan-time read, so anyone holding
  # ec2:CreateRoute — network ops, typically, not whoever runs terraform — could
  # add 0.0.0.0/0 -> igw- to the converter subnet's table and nothing would
  # notice until the next plan. A rule naming 0.0.0.0/0 would make that one edit
  # sufficient for full internet egress; naming the destinations makes it
  # insufficient on its own.
  #
  # The tight form, taken whenever the adopter names the endpoints. An egress
  # rule may name destination security groups, which is evaluated against the
  # ENIs holding them — so this is the KMS and CloudWatch Logs endpoints
  # themselves rather than every address that shares the VPC with them.
  dynamic "egress" {
    for_each = length(var.converter_endpoint_security_group_ids) > 0 ? [1] : []

    content {
      description     = "HTTPS to the S3 gateway endpoint and the named KMS and CloudWatch Logs interface endpoint security groups"
      from_port       = 443
      to_port         = 443
      protocol        = "tcp"
      security_groups = var.converter_endpoint_security_group_ids
      prefix_list_ids = [data.aws_ec2_managed_prefix_list.s3[0].id]
    }
  }

  # The fallback, and its limit stated rather than implied: with no endpoint
  # security groups named the destination is every one of the VPC's associated
  # IPv4 ranges — the whole VPC, not the two endpoint ENIs. Interface-endpoint
  # addresses are assigned at endpoint creation and the endpoints are the
  # adopter's, so they cannot be resolved here; the converter can therefore
  # reach anything in that VPC on 443. var.converter_endpoint_security_group_ids
  # is how an adopter gives that up.
  #
  # cidr_block would be the primary association only. A VPC with secondary CIDR
  # associations exposes the rest through cidr_block_associations, and an
  # endpoint ENI in a subnet carved from one of those would be unreachable —
  # KMS unreachable hangs every conversion to the function timeout, and Logs
  # unreachable takes the diagnostics with it.
  dynamic "egress" {
    for_each = length(var.converter_endpoint_security_group_ids) > 0 ? [] : [1]

    content {
      description     = "HTTPS to the S3 gateway endpoint and the in-VPC KMS and CloudWatch Logs interface endpoints"
      from_port       = 443
      to_port         = 443
      protocol        = "tcp"
      cidr_blocks     = [for a in data.aws_vpc.converter[0].cidr_block_associations : a.cidr_block]
      prefix_list_ids = [data.aws_ec2_managed_prefix_list.s3[0].id]
    }
  }

  tags = local.common_tags
}

# The route table actually associated with each converter subnet (the data
# source resolves the VPC's main table for subnets with no explicit
# association), so the egress claim is measured rather than asserted.
data "aws_route_table" "converter" {
  count = var.enable_doc_conversion ? length(var.converter_subnet_ids) : 0

  subnet_id = var.converter_subnet_ids[count.index]
}

locals {
  # Routes whose next hop leaves the VPC, by the field each one names.
  #
  # gateway_id is an allow list, not a deny list. Gateway VPC endpoints occupy
  # it ("vpce-...") and that is the route the converter needs to reach S3, but
  # so do internet gateways and virtual private gateways ("vgw-", egress to
  # on-premises over VPN or Direct Connect) — so rejecting only "igw-" would
  # pass a vgw silently. Two known-inert values are admitted; anything else,
  # including whatever a later provider version puts there, disqualifies.
  #
  # Every other field below is disqualifying whenever it holds anything.
  # instance_id and network_interface_id matter as much as nat_gateway_id: a
  # NAT *instance*, the pre-NAT-gateway pattern, is expressed as one of those
  # two and grants the same full internet egress.
  #
  # A "vpce-" target gets in only on a prefix-list destination, because two
  # unrelated things wear that prefix. An S3 or DynamoDB *gateway* endpoint is
  # the route the converter needs, and AWS always installs it with
  # destination_prefix_list_id = "pl-..."; a Gateway Load Balancer endpoint is
  # the target of a cidr_block/ipv6_cidr_block route handing traffic to an
  # inspection appliance that forwards it onward, which is full egress. The
  # destination is what tells them apart. The provider splits a "vpce-" target
  # out of the GatewayId the API returns into vpc_endpoint_id, so both fields
  # are read the same way rather than relying on which one it picks.
  #
  # The residual: any prefix-list destination is accepted. The check does not
  # resolve the prefix list and does not confirm it is the AWS-managed
  # com.amazonaws.<region>.s3 or .dynamodb one, so a customer-managed prefix
  # list pointed at a Gateway Load Balancer endpoint passes. What that
  # configuration does not get past is the security group above, which names the
  # S3 managed prefix list by id.
  #
  # "-" is a stand-in for absent: the provider reports an unused field as "",
  # a test override leaves it null, and coalesce skips both.
  converter_egress_routes = [
    for route in flatten([
      for table in data.aws_route_table.converter :
      table.routes == null ? [] : table.routes
    ]) : route
    if(
      (!contains(["-", "local"], coalesce(route.gateway_id, "-")) &&
      !startswith(coalesce(route.gateway_id, "-"), "vpce-")) ||
      ((startswith(coalesce(route.gateway_id, "-"), "vpce-") ||
        coalesce(route.vpc_endpoint_id, "-") != "-") &&
      coalesce(route.destination_prefix_list_id, "-") == "-") ||
      coalesce(route.nat_gateway_id, "-") != "-" ||
      coalesce(route.transit_gateway_id, "-") != "-" ||
      coalesce(route.egress_only_gateway_id, "-") != "-" ||
      coalesce(route.carrier_gateway_id, "-") != "-" ||
      coalesce(route.instance_id, "-") != "-" ||
      coalesce(route.network_interface_id, "-") != "-" ||
      coalesce(route.vpc_peering_connection_id, "-") != "-" ||
      coalesce(route.local_gateway_id, "-") != "-" ||
      coalesce(route.core_network_arn, "-") != "-" ||
      coalesce(route.odb_network_arn, "-") != "-"
    )
  ]
}

resource "terraform_data" "validate_converter_network" {
  count = var.enable_doc_conversion ? 1 : 0

  lifecycle {
    precondition {
      condition     = var.vpc_id != null && length(var.converter_subnet_ids) > 0
      error_message = "enable_doc_conversion = true requires vpc_id and at least one converter_subnet_id. LibreOffice fetches URLs a .doc names, and a Lambda with no vpc_config has full internet egress."
    }

    precondition {
      condition     = length(local.converter_egress_routes) == 0
      error_message = "Every converter_subnet_id's route table may name only a gateway VPC endpoint on a prefix-list destination, or the VPC-local route, as a next hop. Disqualifying: any other gateway_id (internet, virtual private, and anything unrecognised), a VPC endpoint reached by a CIDR route rather than a prefix list, which is how a Gateway Load Balancer endpoint is routed and the appliance behind one forwards onward, NAT gateway, NAT instance by instance id or network interface, egress-only, carrier, transit or Outposts local gateway, peering connection, Cloud WAN core network, or ODB network. Reach S3, KMS and CloudWatch Logs through VPC endpoints instead."
    }

    # See var.converter_endpoint_security_group_ids: left empty, the security
    # group's fallback egress rule targets every CIDR associated with
    # var.vpc_id, not just the KMS and CloudWatch Logs endpoint ENIs. That is
    # reach into the whole of an existing, adopter-owned network (vpc_id is
    # consumed, not created) on 443, held by the one process in this system
    # assumed reachable by a hostile document. Fail closed unless the adopter
    # either names the endpoint security groups or explicitly accepts the
    # wider reach — the loose form must be a deliberate, greppable tfvars
    # declaration, never the silent default.
    precondition {
      condition     = length(var.converter_endpoint_security_group_ids) > 0 || var.converter_accepts_whole_vpc_egress
      error_message = "enable_doc_conversion = true requires converter_endpoint_security_group_ids (naming the KMS and CloudWatch Logs interface endpoints' security groups) or converter_accepts_whole_vpc_egress = true. Left unset, the converter's egress rule falls back to every CIDR associated with vpc_id — the whole of the VPC var.vpc_id names, on 443 — which is reach a compromised LibreOffice process should not have by default."
    }

    # 150 is CONVERT_CONFIG's read timeout (backend/src/rmf_migrator/common/aws_clients.py),
    # not var.converter_timeout_seconds (default 120): Lambda kills the worker
    # at worker_timeout_seconds regardless of what the converter is doing, and
    # a worker cut off mid-invoke never reaches the finally: block that purges
    # convert-scratch/. Lambda does not cancel the converter's RequestResponse
    # invocation when the client gives up, so it keeps running and still
    # writes its .docx — stranding both the source .doc and the converted
    # .docx with no scratch_delete_failed line to trip the alarm this feature
    # already added.
    precondition {
      condition     = var.worker_timeout_seconds > 150
      error_message = "enable_doc_conversion = true requires worker_timeout_seconds > 150 (the CONVERT_CONFIG client read timeout). Lambda kills the worker at worker_timeout_seconds without cancelling an in-flight converter invocation, so a shorter timeout strands both the source .doc and the converted .docx under convert-scratch/ with the purge's finally: block never reached and no scratch_delete_failed alarm to say so."
    }
  }
}

# ---- Function -----------------------------------------------------------------

resource "aws_lambda_function" "converter" {
  # checkov:skip=CKV_AWS_50: X-Ray tracing is left to the adopter, as for every
  # other function in this module.
  # checkov:skip=CKV_AWS_116: A Lambda DLQ applies to asynchronous invocations.
  # The worker invokes this one RequestResponse and owns the failure; the
  # asynchronous path has aws_sqs_queue.parse_dlq.
  # checkov:skip=CKV_AWS_272: Code signing does not apply to container-image
  # functions; the supply-chain control here is the immutable image tag plus
  # scan-on-push.
  count = var.enable_doc_conversion ? 1 : 0

  function_name = "${local.name}-doc-converter"
  role          = aws_iam_role.converter[0].arn
  package_type  = "Image"

  # By digest when the adopter has one (see var.converter_image_digest): the
  # tag form has no plan diff across a delete-then-repush of the same tag, so
  # a digest is the only pin that survives it. Falls back to the tag, which is
  # also the only option before the first image has ever been pushed.
  image_uri = var.converter_image_digest != null ? "${aws_ecr_repository.converter[0].repository_url}@${var.converter_image_digest}" : "${aws_ecr_repository.converter[0].repository_url}:${var.converter_image_tag}"

  # Not a literal: var.converter_timeout_seconds refuses a value that reaches
  # the invoking client's 150s read timeout, which would let botocore give up on
  # a conversion still running and still able to write to purged scratch keys.
  timeout = var.converter_timeout_seconds

  # LibreOffice needs the headroom, and Lambda allocates CPU in proportion to
  # memory — the handler's internal 60s soffice budget assumes a converter this
  # size. Cutting it starves the CPU and turns conversions into timeouts.
  memory_size = 2048

  reserved_concurrent_executions = var.converter_reserved_concurrency

  kms_key_arn = local.kms_key_arn

  environment {
    variables = {
      # The handler refuses any event naming a different bucket, and reads both
      # of these with os.environ[...] — unset means KeyError on every call.
      DOCUMENTS_BUCKET = aws_s3_bucket.documents.id
      KMS_KEY_ID       = local.kms_key_arn
    }
  }

  vpc_config {
    subnet_ids         = var.converter_subnet_ids
    security_group_ids = [aws_security_group.converter[0].id]
  }

  # The preconditions first, so a missing tag or an egress-capable subnet fails
  # with its own message rather than as a provider error somewhere downstream.
  depends_on = [
    aws_cloudwatch_log_group.converter,
    terraform_data.validate_converter_image,
    terraform_data.validate_converter_network,
  ]

  tags = local.common_tags
}

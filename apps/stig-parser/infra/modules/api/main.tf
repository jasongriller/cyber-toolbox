# REST API Gateway. Spec §4.6.
#
# This is a public REGIONAL endpoint. get_config, the stig SPA's /stig GET
# and /stig/{proxy+} GET, and the root GET (the toolbox landing page) are
# the only open (authorization = "NONE") routes — the SPA shell, its config,
# and the front door itself must all be able to load before a user has
# signed in. Every other route sits behind the COGNITO_USER_POOLS authorizer
# below and requires a valid Cognito ID token. There is no gateway-wide
# resource policy; auth decisions live on the individual method resources.

data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  partition         = data.aws_partition.current.partition
  region            = data.aws_region.current.name
  serve_spa_from_s3 = var.spa_serving_mode == "apigw_s3_proxy"

  # Browser Origin excludes the API stage path. Managed SPA modes are served
  # from the API host itself; an externally served SPA must name its
  # exact same-origin facade explicitly.
  managed_upload_cors_origins = var.spa_serving_mode == "none" ? toset([]) : toset([
    regex("^https://[^/]+", aws_api_gateway_stage.this.invoke_url),
  ])
  upload_cors_allowed_origins = setunion(
    local.managed_upload_cors_origins,
    var.additional_upload_cors_origins,
  )
}

# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------

resource "aws_api_gateway_rest_api" "this" {
  #checkov:skip=CKV_AWS_237:create_before_destroy belongs on the deployment, which is the resource that is actually replaced on every routing change. Recreating the REST API itself would change its id and invoke URL out from under every client.
  name        = var.name_prefix
  description = "STIG Condenser API."

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  # S3-proxied SPA assets (fonts, the woff2 files, favicon) and the generated
  # xlsx are binary; without this API Gateway would corrupt them into text.
  binary_media_types = local.serve_spa_from_s3 ? ["*/*"] : []

  tags = merge(var.tags, { Name = var.name_prefix })
}

# ---------------------------------------------------------------------------
# Routes -> API Lambda (proxy integration)
# ---------------------------------------------------------------------------

resource "aws_api_gateway_resource" "config" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "config"
}

resource "aws_api_gateway_resource" "uploads" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "uploads"
}

resource "aws_api_gateway_resource" "jobs" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "jobs"
}

resource "aws_api_gateway_resource" "job" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.jobs.id
  path_part   = "{job_id}"
}

resource "aws_api_gateway_resource" "job_result" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.job.id
  path_part   = "result"
}

resource "aws_api_gateway_resource" "job_cancel" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.job.id
  path_part   = "cancel"
}

locals {
  # The api handler dispatches on (httpMethod, resource), so these keys must stay
  # in step with app/lambdas/api.py.
  routes = {
    get_config   = { resource_id = aws_api_gateway_resource.config.id, method = "GET", open = true }
    post_uploads = { resource_id = aws_api_gateway_resource.uploads.id, method = "POST", open = false }
    post_jobs    = { resource_id = aws_api_gateway_resource.jobs.id, method = "POST", open = false }
    get_job      = { resource_id = aws_api_gateway_resource.job.id, method = "GET", open = false }
    get_result   = { resource_id = aws_api_gateway_resource.job_result.id, method = "GET", open = false }
    post_cancel  = { resource_id = aws_api_gateway_resource.job_cancel.id, method = "POST", open = false }
  }
}

resource "aws_api_gateway_authorizer" "cognito" {
  name            = "${var.name_prefix}-cognito"
  type            = "COGNITO_USER_POOLS"
  rest_api_id     = aws_api_gateway_rest_api.this.id
  provider_arns   = [var.cognito_user_pool_arn]
  identity_source = "method.request.header.Authorization"
}

resource "aws_api_gateway_method" "this" {
  #checkov:skip=CKV_AWS_59:Only get_config is open (open = true): the SPA reads upload limits + AI availability before login, and it exposes no user or job data. Every other route requires a valid Cognito ID token via the authorizer below.
  #checkov:skip=CKV2_AWS_53:Request bodies are validated in the handler (app/lambdas/api.py enforces the shared upload allow-list). A JSON-schema validator at the gateway would duplicate that and drift from it.
  for_each = local.routes

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = each.value.resource_id
  http_method = each.value.method

  authorization = each.value.open ? "NONE" : "COGNITO_USER_POOLS"
  authorizer_id = each.value.open ? null : aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "this" {
  for_each = local.routes

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = each.value.resource_id
  http_method = aws_api_gateway_method.this[each.key].http_method

  type                    = "AWS_PROXY"
  integration_http_method = "POST" # always POST for Lambda proxy, regardless of the route's method
  uri                     = "arn:${local.partition}:apigateway:${local.region}:lambda:path/2015-03-31/functions/${var.api_function_arn}/invocations"
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowInvokeFromApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = var.api_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.this.execution_arn}/*/*"
}

# ---------------------------------------------------------------------------
# SPA (D6) — apigw_s3_proxy mode
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "spa" {
  #checkov:skip=CKV_AWS_18:Access logging belongs on the CUI buckets. This one holds the public-by-nature SPA bundle (JS/CSS/fonts) — every operator fetches all of it on every page load, so an access log records nothing worth keeping.
  #checkov:skip=CKV_AWS_144:GovCloud stays in-region; the bundle is rebuilt by CI anyway.
  #checkov:skip=CKV2_AWS_62:Nothing consumes S3 events on the SPA bucket.
  #checkov:skip=CKV2_AWS_61:The SPA bundle is not time-expiring content — it is replaced wholesale on each deploy, and versioning bounds the history.
  count = local.serve_spa_from_s3 ? 1 : 0

  bucket = "${var.name_prefix}-spa"
  tags   = merge(var.tags, { Name = "${var.name_prefix}-spa" })
}

resource "aws_s3_bucket_public_access_block" "spa" {
  count = local.serve_spa_from_s3 ? 1 : 0

  bucket                  = aws_s3_bucket.spa[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "spa" {
  count = local.serve_spa_from_s3 ? 1 : 0

  bucket = aws_s3_bucket.spa[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "spa" {
  count = local.serve_spa_from_s3 ? 1 : 0

  bucket = aws_s3_bucket.spa[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

# Match the TLS-only + ACLs-disabled posture the CUI buckets carry (storage
# module). The bundle is public-by-nature, but the bucket's own policy should
# still reject plaintext-HTTP access and disable ACLs rather than relying on
# defaults.
resource "aws_s3_bucket_ownership_controls" "spa" {
  count = local.serve_spa_from_s3 ? 1 : 0

  bucket = aws_s3_bucket.spa[0].id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

data "aws_iam_policy_document" "spa_bucket" {
  count = local.serve_spa_from_s3 ? 1 : 0

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.spa[0].arn, "${aws_s3_bucket.spa[0].arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "spa" {
  count = local.serve_spa_from_s3 ? 1 : 0

  bucket = aws_s3_bucket.spa[0].id
  policy = data.aws_iam_policy_document.spa_bucket[0].json
}

# API Gateway reads the bucket under this role — the browser never talks to S3
# for SPA assets, so the bucket stays fully private.
data "aws_iam_policy_document" "spa_assume" {
  count = local.serve_spa_from_s3 ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["apigateway.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "spa_read" {
  count = local.serve_spa_from_s3 ? 1 : 0

  statement {
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.spa[0].arn}/*"]
  }

  statement {
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role" "spa" {
  count = local.serve_spa_from_s3 ? 1 : 0

  name               = "${var.name_prefix}-spa-read"
  assume_role_policy = data.aws_iam_policy_document.spa_assume[0].json
  tags               = var.tags
}

resource "aws_iam_role_policy" "spa" {
  count = local.serve_spa_from_s3 ? 1 : 0

  name   = "${var.name_prefix}-spa-read"
  role   = aws_iam_role.spa[0].id
  policy = data.aws_iam_policy_document.spa_read[0].json
}

# /stig — the stig SPA's own shell route now that it has moved off the bare
# stage URL (which serves the toolbox landing page instead, below). {proxy+}
# resolves concrete paths only, so /stig needs this dedicated GET the same
# way the API root did before the move.
resource "aws_api_gateway_resource" "stig" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "stig"
}

resource "aws_api_gateway_method" "stig" {
  #checkov:skip=CKV_AWS_59:Serves the stig SPA shell at /stig — must load before a user can authenticate; all data routes carry the Cognito authorizer.
  #checkov:skip=CKV2_AWS_53:A parameterless GET for the SPA shell has no request body to validate.
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id   = aws_api_gateway_rest_api.this.id
  resource_id   = aws_api_gateway_resource.stig[0].id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "stig" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.stig[0].id
  http_method = aws_api_gateway_method.stig[0].http_method

  type                    = "AWS"
  integration_http_method = "GET"
  uri                     = "arn:${local.partition}:apigateway:${local.region}:s3:path/${aws_s3_bucket.spa[0].bucket}/index.html"
  credentials             = aws_iam_role.spa[0].arn
}

resource "aws_api_gateway_method_response" "stig" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.stig[0].id
  http_method = aws_api_gateway_method.stig[0].http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Content-Type" = true
  }
}

resource "aws_api_gateway_integration_response" "stig" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.stig[0].id
  http_method = aws_api_gateway_method.stig[0].http_method
  status_code = aws_api_gateway_method_response.stig[0].status_code

  response_parameters = {
    "method.response.header.Content-Type" = "integration.response.header.Content-Type"
  }

  depends_on = [aws_api_gateway_integration.stig]
}

# Nested under /stig (not the API root) — the stig SPA moved off the bare
# stage URL, which now serves the toolbox landing page instead. Assets are
# requested relative to /stig/ (the bundle is built with base: './'), so the
# proxy has to live under the same parent as the shell above.
resource "aws_api_gateway_resource" "spa_proxy" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.stig[0].id
  path_part   = "{proxy+}"
}

resource "aws_api_gateway_method" "spa_proxy" {
  #checkov:skip=CKV_AWS_59:Serves the static SPA bundle (JS/CSS/fonts) — the login shell itself. It must load before a user can authenticate; all job/data routes sit behind the Cognito authorizer.
  #checkov:skip=CKV2_AWS_53:A GET for a static asset by path has no request body to validate.
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id   = aws_api_gateway_rest_api.this.id
  resource_id   = aws_api_gateway_resource.spa_proxy[0].id
  http_method   = "GET"
  authorization = "NONE"

  request_parameters = {
    "method.request.path.proxy" = true
  }
}

resource "aws_api_gateway_integration" "spa_proxy" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.spa_proxy[0].id
  http_method = aws_api_gateway_method.spa_proxy[0].http_method

  type                    = "AWS"
  integration_http_method = "GET"
  uri                     = "arn:${local.partition}:apigateway:${local.region}:s3:path/${aws_s3_bucket.spa[0].bucket}/{proxy}"
  credentials             = aws_iam_role.spa[0].arn

  request_parameters = {
    "integration.request.path.proxy" = "method.request.path.proxy"
  }
}

resource "aws_api_gateway_method_response" "spa_proxy" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.spa_proxy[0].id
  http_method = aws_api_gateway_method.spa_proxy[0].http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Content-Type" = true
  }
}

resource "aws_api_gateway_integration_response" "spa_proxy" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.spa_proxy[0].id
  http_method = aws_api_gateway_method.spa_proxy[0].http_method
  status_code = aws_api_gateway_method_response.spa_proxy[0].status_code

  response_parameters = {
    "method.response.header.Content-Type" = "integration.response.header.Content-Type"
  }

  depends_on = [aws_api_gateway_integration.spa_proxy]
}

# ---------------------------------------------------------------------------
# /rmf — the rmf SPA (Phase R) + its own API, HTTP_PROXY'd straight through
# ---------------------------------------------------------------------------

# /rmf — the rmf SPA's own shell route, the second tool behind the toolbox
# front door. {proxy+} resolves concrete paths only, so /rmf needs this
# dedicated GET the same way /stig does above.
resource "aws_api_gateway_resource" "rmf" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "rmf"
}

resource "aws_api_gateway_method" "rmf" {
  #checkov:skip=CKV_AWS_59:Serves the rmf SPA shell at /rmf — must load before a user can authenticate; the rmf API proxy below (and every route behind it) requires a valid Cognito JWT.
  #checkov:skip=CKV2_AWS_53:A parameterless GET for the SPA shell has no request body to validate.
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id   = aws_api_gateway_rest_api.this.id
  resource_id   = aws_api_gateway_resource.rmf[0].id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "rmf" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.rmf[0].id
  http_method = aws_api_gateway_method.rmf[0].http_method

  type                    = "AWS"
  integration_http_method = "GET"
  uri                     = "arn:${local.partition}:apigateway:${local.region}:s3:path/${aws_s3_bucket.spa[0].bucket}/rmf/index.html"
  credentials             = aws_iam_role.spa[0].arn
}

resource "aws_api_gateway_method_response" "rmf" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.rmf[0].id
  http_method = aws_api_gateway_method.rmf[0].http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Content-Type" = true
  }
}

resource "aws_api_gateway_integration_response" "rmf" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.rmf[0].id
  http_method = aws_api_gateway_method.rmf[0].http_method
  status_code = aws_api_gateway_method_response.rmf[0].status_code

  response_parameters = {
    "method.response.header.Content-Type" = "integration.response.header.Content-Type"
  }

  depends_on = [aws_api_gateway_integration.rmf]
}

# Nested under /rmf (not the API root) — unlike spa_proxy above (stig's
# bundle uses base: './', so its assets are requested relative to /stig/),
# the rmf bundle is built with VITE_BASE_PATH set to the stage path plus
# /rmf/ (e.g. /v1/rmf/), which Vite emits as ABSOLUTE asset URLs (e.g.
# /v1/rmf/assets/index-HASH.js) — this resource is what makes those absolute
# paths resolve.
resource "aws_api_gateway_resource" "rmf_proxy" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.rmf[0].id
  path_part   = "{proxy+}"
}

resource "aws_api_gateway_method" "rmf_proxy" {
  #checkov:skip=CKV_AWS_59:Serves the static rmf SPA bundle (JS/CSS/fonts) — the login shell itself. It must load before a user can authenticate; every rmf API route sits behind rmf's own Cognito JWT authorizer.
  #checkov:skip=CKV2_AWS_53:A GET for a static asset by path has no request body to validate.
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id   = aws_api_gateway_rest_api.this.id
  resource_id   = aws_api_gateway_resource.rmf_proxy[0].id
  http_method   = "GET"
  authorization = "NONE"

  request_parameters = {
    "method.request.path.proxy" = true
  }
}

resource "aws_api_gateway_integration" "rmf_proxy" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.rmf_proxy[0].id
  http_method = aws_api_gateway_method.rmf_proxy[0].http_method

  type                    = "AWS"
  integration_http_method = "GET"
  uri                     = "arn:${local.partition}:apigateway:${local.region}:s3:path/${aws_s3_bucket.spa[0].bucket}/rmf/{proxy}"
  credentials             = aws_iam_role.spa[0].arn

  request_parameters = {
    "integration.request.path.proxy" = "method.request.path.proxy"
  }
}

resource "aws_api_gateway_method_response" "rmf_proxy" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.rmf_proxy[0].id
  http_method = aws_api_gateway_method.rmf_proxy[0].http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Content-Type" = true
  }
}

resource "aws_api_gateway_integration_response" "rmf_proxy" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.rmf_proxy[0].id
  http_method = aws_api_gateway_method.rmf_proxy[0].http_method
  status_code = aws_api_gateway_method_response.rmf_proxy[0].status_code

  response_parameters = {
    "method.response.header.Content-Type" = "integration.response.header.Content-Type"
  }

  depends_on = [aws_api_gateway_integration.rmf_proxy]
}

# /rmf/api — HTTP_PROXY straight through to rmf's own HTTP API (url from SSM;
# see the data "aws_ssm_parameter" "rmf_api_url" block in envs/*/main.tf). A
# literal sibling of rmf_proxy's {proxy+} under the same /rmf parent: API
# Gateway always prefers a literal path part over a greedy path-parameter
# sibling at the same tree level, so a request to /rmf/api/... resolves here
# (and from here into rmf_api_proxy below), never into rmf_proxy's S3 read —
# even though /rmf/{proxy+} would otherwise greedily match "api" too.
#
# authorization = "NONE" is deliberate, not an oversight: rmf's own HTTP API
# (apps/rmf-migrator/terraform/modules/rmf-migrator/apigateway.tf) already
# carries its own JWT authorizer against the same shared Cognito pool and
# enforces it on every route it defines. Adding a second Cognito authorizer at
# this hop would be a redundant, drift-prone copy of a decision already made
# downstream — two auth checks that could silently disagree. This proxy's job
# is transport only; rmf's own gateway is the actual enforcement point.
resource "aws_api_gateway_resource" "rmf_api" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.rmf[0].id
  path_part   = "api"
}

resource "aws_api_gateway_method" "rmf_api" {
  #checkov:skip=CKV_AWS_59:Deliberately open at this hop — rmf's own HTTP API carries its own Cognito JWT authorizer and enforces it on every route (see the resource comment above). A second authorizer here would duplicate, not strengthen, a decision already made downstream.
  #checkov:skip=CKV2_AWS_53:This is a byte-for-byte proxy fronting rmf's entire API surface, not one fixed request shape — there is no single schema to validate against here. rmf's own Lambda handlers validate their own request bodies on the far side of the proxy.
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id   = aws_api_gateway_rest_api.this.id
  resource_id   = aws_api_gateway_resource.rmf_api[0].id
  http_method   = "ANY"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "rmf_api" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.rmf_api[0].id
  http_method = aws_api_gateway_method.rmf_api[0].http_method

  type                    = "HTTP_PROXY"
  integration_http_method = "ANY"
  uri                     = var.rmf_api_url
}

resource "aws_api_gateway_resource" "rmf_api_proxy" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.rmf_api[0].id
  path_part   = "{proxy+}"
}

resource "aws_api_gateway_method" "rmf_api_proxy" {
  #checkov:skip=CKV_AWS_59:Same rationale as rmf_api above — rmf's own HTTP API enforces Cognito JWT auth on every route it defines; this hop is transport only.
  #checkov:skip=CKV2_AWS_53:Same rationale as rmf_api above — a generic proxy for rmf's entire API surface has no single request shape to validate against here.
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id   = aws_api_gateway_rest_api.this.id
  resource_id   = aws_api_gateway_resource.rmf_api_proxy[0].id
  http_method   = "ANY"
  authorization = "NONE"

  request_parameters = {
    "method.request.path.proxy" = true
  }
}

resource "aws_api_gateway_integration" "rmf_api_proxy" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.rmf_api_proxy[0].id
  http_method = aws_api_gateway_method.rmf_api_proxy[0].http_method

  type                    = "HTTP_PROXY"
  integration_http_method = "ANY"
  uri                     = "${var.rmf_api_url}/{proxy}"

  request_parameters = {
    "integration.request.path.proxy" = "method.request.path.proxy"
  }
}

# The bare stage URL is the toolbox front door — the link users get is just
# the invoke URL. {proxy+} matches concrete paths only, so the root needs
# its own method; same private-bucket read role, pinned to the landing
# page's key (kept alongside the SPA bundle in the same bucket — see
# deploy.sh's SPA-sync phase, which publishes this exact key).
resource "aws_api_gateway_method" "spa_root" {
  #checkov:skip=CKV_AWS_59:Serves the toolbox landing page at the bare URL — must load before a user has signed in or chosen a tool; all data routes carry the Cognito authorizer.
  #checkov:skip=CKV2_AWS_53:A parameterless GET for the landing page has no request body to validate.
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id   = aws_api_gateway_rest_api.this.id
  resource_id   = aws_api_gateway_rest_api.this.root_resource_id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "spa_root" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_rest_api.this.root_resource_id
  http_method = aws_api_gateway_method.spa_root[0].http_method

  type                    = "AWS"
  integration_http_method = "GET"
  uri                     = "arn:${local.partition}:apigateway:${local.region}:s3:path/${aws_s3_bucket.spa[0].bucket}/landing/index.html"
  credentials             = aws_iam_role.spa[0].arn
}

resource "aws_api_gateway_method_response" "spa_root" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_rest_api.this.root_resource_id
  http_method = aws_api_gateway_method.spa_root[0].http_method
  status_code = "200"

  response_parameters = {
    "method.response.header.Content-Type" = true
  }
}

resource "aws_api_gateway_integration_response" "spa_root" {
  count = local.serve_spa_from_s3 ? 1 : 0

  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_rest_api.this.root_resource_id
  http_method = aws_api_gateway_method.spa_root[0].http_method
  status_code = aws_api_gateway_method_response.spa_root[0].status_code

  response_parameters = {
    "method.response.header.Content-Type" = "integration.response.header.Content-Type"
  }

  depends_on = [aws_api_gateway_integration.spa_root]
}

# ---------------------------------------------------------------------------
# Deployment + stage
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "access" {
  name              = "/aws/apigateway/${var.name_prefix}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = merge(var.tags, { Name = "${var.name_prefix}-api-access" })
}

resource "aws_api_gateway_deployment" "this" {
  rest_api_id = aws_api_gateway_rest_api.this.id

  # Redeploy whenever the routing surface changes. Without this the API keeps
  # serving the previous definition after an apply that looked successful.
  #
  # The `.uri` entries matter as much as the `.id` ones: aws_api_gateway_
  # integration's id is a rest_api_id/resource_id/http_method composite, so a
  # URI-only change (e.g. repointing spa_root at a different S3 key) would
  # NOT change its id and would silently fail to trigger a redeploy without
  # `.uri` captured explicitly here.
  #
  # The `.path` entries exist for the same reason, one level up: re-parenting
  # an aws_api_gateway_resource (parent_id) is an in-place PATCH, not a
  # replacement — the resource keeps its id. A future re-parent-only change
  # (Phase R's /rmf work is the obvious candidate) would change neither
  # resource id nor any integration id/uri above, and would silently apply
  # with no redeploy without `.path` (computed from the live parent chain)
  # captured here.
  #
  # The rmf/rmf_proxy/rmf_api/rmf_api_proxy entries below follow the same
  # full-coverage shape as stig's (id + path + authorization + integration id
  # + integration uri) rather than spa_proxy/spa_root's sparser one: the
  # rmf_api/rmf_api_proxy integrations are HTTP_PROXY with a `.uri` that
  # repoints whenever var.rmf_api_url changes (e.g. rmf's HTTP API is
  # recreated) without necessarily changing any resource/method/integration
  # id — exactly the URI-only-repoint gap called out above, caught for real
  # once already this phase.
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_rest_api.this.body,
      [for k, m in aws_api_gateway_method.this : m.id],
      [for k, m in aws_api_gateway_method.this : [m.authorization, m.authorizer_id]],
      aws_api_gateway_authorizer.cognito.id,
      [for k, i in aws_api_gateway_integration.this : i.id],
      local.serve_spa_from_s3 ? aws_api_gateway_integration.spa_proxy[0].id : "",
      local.serve_spa_from_s3 ? aws_api_gateway_resource.spa_proxy[0].path : "",
      local.serve_spa_from_s3 ? aws_api_gateway_integration.spa_root[0].id : "",
      local.serve_spa_from_s3 ? aws_api_gateway_integration.spa_root[0].uri : "",
      local.serve_spa_from_s3 ? aws_api_gateway_method.spa_root[0].authorization : "",
      local.serve_spa_from_s3 ? aws_api_gateway_resource.stig[0].id : "",
      local.serve_spa_from_s3 ? aws_api_gateway_resource.stig[0].path : "",
      local.serve_spa_from_s3 ? aws_api_gateway_method.stig[0].authorization : "",
      local.serve_spa_from_s3 ? aws_api_gateway_integration.stig[0].id : "",
      local.serve_spa_from_s3 ? aws_api_gateway_integration.stig[0].uri : "",
      local.serve_spa_from_s3 ? aws_api_gateway_resource.rmf[0].id : "",
      local.serve_spa_from_s3 ? aws_api_gateway_resource.rmf[0].path : "",
      local.serve_spa_from_s3 ? aws_api_gateway_method.rmf[0].authorization : "",
      local.serve_spa_from_s3 ? aws_api_gateway_integration.rmf[0].id : "",
      local.serve_spa_from_s3 ? aws_api_gateway_integration.rmf[0].uri : "",
      local.serve_spa_from_s3 ? aws_api_gateway_resource.rmf_proxy[0].id : "",
      local.serve_spa_from_s3 ? aws_api_gateway_resource.rmf_proxy[0].path : "",
      local.serve_spa_from_s3 ? aws_api_gateway_method.rmf_proxy[0].authorization : "",
      local.serve_spa_from_s3 ? aws_api_gateway_integration.rmf_proxy[0].id : "",
      local.serve_spa_from_s3 ? aws_api_gateway_integration.rmf_proxy[0].uri : "",
      local.serve_spa_from_s3 ? aws_api_gateway_resource.rmf_api[0].id : "",
      local.serve_spa_from_s3 ? aws_api_gateway_resource.rmf_api[0].path : "",
      local.serve_spa_from_s3 ? aws_api_gateway_method.rmf_api[0].authorization : "",
      local.serve_spa_from_s3 ? aws_api_gateway_integration.rmf_api[0].id : "",
      local.serve_spa_from_s3 ? aws_api_gateway_integration.rmf_api[0].uri : "",
      local.serve_spa_from_s3 ? aws_api_gateway_resource.rmf_api_proxy[0].id : "",
      local.serve_spa_from_s3 ? aws_api_gateway_resource.rmf_api_proxy[0].path : "",
      local.serve_spa_from_s3 ? aws_api_gateway_method.rmf_api_proxy[0].authorization : "",
      local.serve_spa_from_s3 ? aws_api_gateway_integration.rmf_api_proxy[0].id : "",
      local.serve_spa_from_s3 ? aws_api_gateway_integration.rmf_api_proxy[0].uri : "",
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.this,
    aws_api_gateway_integration.spa_proxy,
    aws_api_gateway_integration.spa_root,
    aws_api_gateway_integration.stig,
    aws_api_gateway_integration.rmf,
    aws_api_gateway_integration.rmf_proxy,
    aws_api_gateway_integration.rmf_api,
    aws_api_gateway_integration.rmf_api_proxy,
  ]
}

resource "aws_api_gateway_stage" "this" {
  #checkov:skip=CKV2_AWS_51:Client-certificate auth is for the gateway proving its identity to a BACKEND. The backend here is Lambda, invoked over the AWS API with SigV4 — there is no origin to present a certificate to.
  #checkov:skip=CKV_AWS_120:Response caching is off deliberately. Job status is polled and must be fresh, and caching would put CUI in a gateway-managed cache.
  #checkov:skip=CKV_AWS_73:X-Ray is off by design — see the compute module.
  #checkov:skip=CKV_AWS_237:create_before_destroy is set on the deployment, which is the resource that actually gets replaced.
  rest_api_id   = aws_api_gateway_rest_api.this.id
  deployment_id = aws_api_gateway_deployment.this.id
  stage_name    = var.stage_name

  xray_tracing_enabled = false

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    # No request body, no query strings: this API carries CUI filenames, and an
    # access log is not an appropriate place for them.
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      resourcePath   = "$context.resourcePath"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      integrationErr = "$context.integration.error"
    })
  }

  tags = merge(var.tags, { Name = var.name_prefix })
}

resource "aws_api_gateway_method_settings" "this" {
  #checkov:skip=CKV_AWS_225:Caching is off deliberately — job status must be fresh, and a gateway cache would hold CUI.
  rest_api_id = aws_api_gateway_rest_api.this.id
  stage_name  = aws_api_gateway_stage.this.stage_name
  method_path = "*/*"

  settings {
    throttling_rate_limit  = var.throttling_rate_limit
    throttling_burst_limit = var.throttling_burst_limit
    logging_level          = "ERROR"
    # Request/response bodies would land CUI in CloudWatch.
    data_trace_enabled = false
    metrics_enabled    = true
  }
}

# The browser uploads directly to a presigned S3 URL. CORS is an independent
# browser control and grants no S3 permission; IAM and the endpoint policy still
# authorize the signed PUT. The client sets no custom headers, although user
# agents can synthesize Content-Type from File.type.
resource "aws_s3_bucket_cors_configuration" "uploads" {
  bucket = var.uploads_bucket_name

  cors_rule {
    allowed_methods = ["PUT"]
    allowed_headers = ["Content-Type"]
    allowed_origins = sort(tolist(local.upload_cors_allowed_origins))
    max_age_seconds = 300
  }

  lifecycle {
    precondition {
      condition     = length(local.upload_cors_allowed_origins) > 0
      error_message = "At least one exact upload CORS origin is required; spa_serving_mode=none must set additional_upload_cors_origins."
    }
  }
}

# HTTP API (API Gateway v2) fronting the API Lambdas.
#
# This defines the REST surface the SPA and any toolbox integrator calls. In
# "private" mode the API requires AWS Signature Version 4 authentication and
# the Lambdas run in the adopter's VPC. HTTP API v2 does not support a PRIVATE
# endpoint type, so its AWS edge hostname still resolves publicly; unauthenticated
# requests are rejected. Put an internal signing proxy in front when the browser
# must not call the AWS hostname directly.
#
# Route authorization is governed by auth_mode, independently of network_mode:
# "iam" requires SigV4, "cognito" requires a valid Cognito-issued JWT (the
# posture for a public, VPC-free deployment that still must not be anonymous),
# and "none" leaves every route open (dev/demo only, never for CUI).

locals {
  routes = {
    "POST /projects"                                            = "create-project"
    "GET /projects"                                             = "list-projects"
    "DELETE /projects/{project_id}"                             = "delete-project"
    "GET /projects/{project_id}/documents"                      = "list-documents"
    "POST /projects/{project_id}/documents"                     = "request-upload"
    "POST /projects/{project_id}/documents/{document_id}/parse" = "enqueue-parse"
    "GET /projects/{project_id}/jobs/{job_id}"                  = "get-job"

    # Mapping review (M2)
    "GET /projects/{project_id}/documents/{document_id}"                       = "get-document"
    "GET /projects/{project_id}/documents/{document_id}/sections"              = "list-sections"
    "GET /projects/{project_id}/documents/{document_id}/mappings"              = "get-mappings"
    "PUT /projects/{project_id}/documents/{document_id}/mappings/{section_id}" = "update-mapping"
    "POST /projects/{project_id}/documents/{document_id}/mappings/approve"     = "approve-mappings"

    # Rev 5 drafting + chat (M3)
    "GET /projects/{project_id}/documents/{document_id}/drafts"                       = "get-drafts"
    "PUT /projects/{project_id}/documents/{document_id}/drafts/{section_id}"          = "update-draft"
    "POST /projects/{project_id}/documents/{document_id}/drafts/{section_id}/approve" = "approve-draft"
    "POST /projects/{project_id}/documents/{document_id}/sections/{section_id}/chat"  = "chat"

    # Rev 5 export + decision log (M4)
    "POST /projects/{project_id}/documents/{document_id}/export"          = "start-export"
    "GET /projects/{project_id}/export-jobs/{job_id}"                     = "get-export-job"
    "GET /projects/{project_id}/documents/{document_id}/export/download"  = "download-export"
    "GET /projects/{project_id}/documents/{document_id}/decision-log.csv" = "decision-log"

    # Coverage dashboard + conversion matrix (M5)
    "GET /projects/{project_id}/coverage"              = "coverage"
    "GET /projects/{project_id}/conversion-matrix.csv" = "conversion-matrix"
    "GET /projects/{project_id}/oscal.json"            = "oscal"
    "GET /projects/{project_id}/emass.csv"             = "emass-export"
  }
}

resource "aws_apigatewayv2_api" "this" {
  name          = "${local.name}-api"
  protocol_type = "HTTP"

  # CSP frame-ancestors is enforced at the SPA delivery layer; CORS here governs
  # XHR from the SPA origin(s).
  cors_configuration {
    allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    # Private-mode validation requires an explicit allowlist. The wildcard is
    # reachable only in the deliberately unauthenticated public demo posture.
    allow_origins = length(var.frame_ancestors) > 0 ? var.frame_ancestors : ["*"]
    allow_headers = [
      "content-type",
      "authorization",
      "x-amz-date",
      "x-amz-security-token",
      "x-amz-content-sha256",
      var.identity_header != null ? var.identity_header : "x-remote-user",
    ]
    max_age = 3000
  }

  tags = local.common_tags
}

resource "aws_apigatewayv2_integration" "api" {
  for_each = aws_lambda_function.api

  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = each.value.invoke_arn
  payload_format_version = "2.0"
}

# JWT authorizer for auth_mode = "cognito": a public (VPC-free) deployment that
# still must not be anonymous. Created only in that posture.
resource "aws_apigatewayv2_authorizer" "cognito" {
  count = local.auth_mode == "cognito" ? 1 : 0

  api_id           = aws_apigatewayv2_api.this.id
  name             = "${local.name}-cognito"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    audience = [var.cognito_client_id]
    # MUST be the non-FIPS hostname (cognito-idp., never cognito-idp-fips.).
    # Cognito always stamps the non-FIPS host into a token's `iss` claim, no
    # matter which hostname issued the token — including this deployment's own
    # SDK login calls, which use the FIPS hostname (cognito-idp-fips.). If this
    # issuer used the FIPS host instead, it would never match a real token's
    # iss, and the authorizer would silently 401 every login: nothing short of
    # an actual human sign-in attempt surfaces it, since forged-token or
    # API-Gateway-console checks don't exercise a real Cognito-issued iss claim.
    issuer = "https://cognito-idp.${local.region}.amazonaws.com/${var.cognito_user_pool_id}"
  }
}

resource "aws_apigatewayv2_route" "this" {
  for_each = local.routes

  # checkov:skip=CKV_AWS_309: AWS_IAM and JWT (Cognito) both require an
  # authenticated caller on every route. NONE is retained only for the
  # explicitly selected public dev/demo mode, which the module and deployment
  # guide prohibit for CUI.
  # Private mode requires SigV4 on every route (auth_mode "iam"). Public mode
  # has no default — the operator must explicitly pick "cognito" (login without
  # a VPC), "iam", or the dev/demo-only "none" (enforced in main.tf).
  api_id    = aws_apigatewayv2_api.this.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.api[each.value].id}"
  authorization_type = (
    local.auth_mode == "iam" ? "AWS_IAM" : (local.auth_mode == "cognito" ? "JWT" : "NONE")
  )
  authorizer_id = local.auth_mode == "cognito" ? aws_apigatewayv2_authorizer.cognito[0].id : null
}

resource "aws_cloudwatch_log_group" "apigw" {
  # checkov:skip=CKV_AWS_338: Retention is set by var.log_retention_days; access
  # logs carry request metadata only, never bodies.
  name              = "/aws/apigateway/${local.name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = local.kms_key_arn
  tags              = local.common_tags
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.apigw.arn
    # Access logs carry request metadata only — never bodies.
    format = jsonencode({
      requestId      = "$context.requestId"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      sourceIp       = "$context.identity.sourceIp"
    })
  }

  tags = local.common_tags
}

resource "aws_lambda_permission" "apigw" {
  for_each = aws_lambda_function.api

  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = each.value.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}

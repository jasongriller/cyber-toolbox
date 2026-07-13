# HTTP API (API Gateway v2) fronting the API Lambdas.
#
# This defines the REST surface the SPA and any toolbox integrator calls. In
# "public" mode the default endpoint is reachable directly (dev/demo). In
# "private" mode the adopter fronts this API from inside their network — via an
# internal ALB or an execute-api VPC endpoint — so it is not used from the public
# internet. That front-door wiring is completed alongside the frontend milestone;
# the routes/integrations below are identical for both modes.

locals {
  routes = {
    "POST /projects"                                            = "create-project"
    "POST /projects/{project_id}/documents"                    = "request-upload"
    "POST /projects/{project_id}/documents/{document_id}/parse" = "enqueue-parse"
    "GET /projects/{project_id}/jobs/{job_id}"                 = "get-job"

    # Mapping review (M2)
    "GET /projects/{project_id}/documents/{document_id}"                    = "get-document"
    "GET /projects/{project_id}/documents/{document_id}/sections"          = "list-sections"
    "GET /projects/{project_id}/documents/{document_id}/mappings"          = "get-mappings"
    "PUT /projects/{project_id}/documents/{document_id}/mappings/{section_id}" = "update-mapping"
    "POST /projects/{project_id}/documents/{document_id}/mappings/approve" = "approve-mappings"

    # Rev 5 drafting + chat (M3)
    "GET /projects/{project_id}/documents/{document_id}/drafts"                       = "get-drafts"
    "PUT /projects/{project_id}/documents/{document_id}/drafts/{section_id}"          = "update-draft"
    "POST /projects/{project_id}/documents/{document_id}/drafts/{section_id}/approve" = "approve-draft"
    "POST /projects/{project_id}/documents/{document_id}/sections/{section_id}/chat"  = "chat"

    # Rev 5 export + decision log (M4)
    "POST /projects/{project_id}/documents/{document_id}/export"            = "start-export"
    "GET /projects/{project_id}/export-jobs/{job_id}"                      = "get-export-job"
    "GET /projects/{project_id}/documents/{document_id}/export/download"   = "download-export"
    "GET /projects/{project_id}/documents/{document_id}/decision-log.csv"  = "decision-log"
  }
}

resource "aws_apigatewayv2_api" "this" {
  name          = "${local.name}-api"
  protocol_type = "HTTP"

  # CSP frame-ancestors is enforced at the SPA delivery layer; CORS here governs
  # XHR from the SPA origin(s).
  cors_configuration {
    allow_methods = ["GET", "POST", "PUT", "OPTIONS"]
    allow_origins = length(var.frame_ancestors) > 0 ? var.frame_ancestors : ["*"]
    allow_headers = ["content-type", var.identity_header != null ? var.identity_header : "x-remote-user"]
    max_age       = 3000
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

resource "aws_apigatewayv2_route" "this" {
  for_each = local.routes

  api_id    = aws_apigatewayv2_api.this.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.api[each.value].id}"
}

resource "aws_cloudwatch_log_group" "apigw" {
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

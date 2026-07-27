mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = {
      partition = "aws-us-gov"
    }
  }

  mock_data "aws_region" {
    defaults = {
      name = "us-gov-west-1"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  mock_resource "aws_api_gateway_stage" {
    defaults = {
      invoke_url = "https://api-test.execute-api.us-gov-west-1.amazonaws.com/v1"
    }
  }

  mock_resource "aws_api_gateway_rest_api" {
    defaults = {
      execution_arn = "arn:aws-us-gov:execute-api:us-gov-west-1:aws:api-test"
    }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws-us-gov:logs:us-gov-west-1:aws:log-group:test"
    }
  }
}

variables {
  name_prefix = "stig-condenser-test"
  # Mock pool ARN: the aws provider validates the account slot client-side (12
  # digits or a recognized token; "cw" + 10 chars is the only digit-free
  # form), and a digit-free value keeps the CI leak grep quiet.
  cognito_user_pool_arn = "arn:aws-us-gov:cognito-idp:us-gov-west-1:cwmockacctid:userpool/us-gov-west-1_MOCK"
  api_function_arn      = "arn:aws-us-gov:lambda:us-gov-west-1:test:function:api-test"
  api_function_name     = "api-test"
  uploads_bucket_name   = "test-uploads"
  kms_key_arn           = "arn:aws-us-gov:kms:us-gov-west-1:test:key/test"
  # rmf's own $default-stage HTTP API endpoint has no stage path segment.
  rmf_api_url = "https://rmf-api-test.execute-api.us-gov-west-1.amazonaws.com"
}

# The route auth split is the flip's security boundary: get_config, the
# toolbox landing page (root), and the stig SPA shell (/stig and its asset
# proxy) must all stay reachable before login, and every route that touches
# uploads or job data must sit behind the Cognito authorizer. This runs with
# spa_serving_mode left at its apigw_s3_proxy default (as upload_cors.tftest's
# base run does) so aws_api_gateway_method.spa_proxy[0] and
# aws_api_gateway_method.stig[0] exist to assert on.
#
# command = apply, not plan: authorizer_id is a cross-resource reference to
# aws_api_gateway_authorizer.cognito.id, a computed value that stays unknown
# at plan time. Only apply materializes it under the mock provider.
run "route_auth_matches_the_security_boundary" {
  command = apply

  assert {
    condition     = aws_api_gateway_method.this["get_config"].authorization == "NONE"
    error_message = "get_config must stay open (NONE): the route auth split is the flip's security boundary, and the SPA reads upload limits and AI availability before a user signs in."
  }

  assert {
    condition     = aws_api_gateway_method.this["get_config"].authorizer_id == null
    error_message = "get_config must carry no authorizer_id: the route auth split is the flip's security boundary, and an open route wired to the Cognito authorizer would be a contradiction waiting to bite."
  }

  assert {
    condition     = aws_api_gateway_method.this["post_uploads"].authorization == "COGNITO_USER_POOLS"
    error_message = "post_uploads must require COGNITO_USER_POOLS: the route auth split is the flip's security boundary, and upload-URL issuance must not go anonymous now that the API is public."
  }

  assert {
    condition     = aws_api_gateway_method.this["post_uploads"].authorizer_id != null
    error_message = "post_uploads must be wired to the Cognito authorizer: the route auth split is the flip's security boundary, and a null authorizer_id here means the check never actually runs."
  }

  assert {
    condition     = aws_api_gateway_method.this["post_jobs"].authorization == "COGNITO_USER_POOLS"
    error_message = "post_jobs must require COGNITO_USER_POOLS: the route auth split is the flip's security boundary, and job creation must not go anonymous now that the API is public."
  }

  assert {
    condition     = aws_api_gateway_method.this["post_jobs"].authorizer_id != null
    error_message = "post_jobs must be wired to the Cognito authorizer: the route auth split is the flip's security boundary, and a null authorizer_id here means the check never actually runs."
  }

  assert {
    condition     = aws_api_gateway_method.this["get_job"].authorization == "COGNITO_USER_POOLS"
    error_message = "get_job must require COGNITO_USER_POOLS: the route auth split is the flip's security boundary, and job status must not be readable anonymously now that the API is public."
  }

  assert {
    condition     = aws_api_gateway_method.this["get_job"].authorizer_id != null
    error_message = "get_job must be wired to the Cognito authorizer: the route auth split is the flip's security boundary, and a null authorizer_id here means the check never actually runs."
  }

  assert {
    condition     = aws_api_gateway_method.this["get_result"].authorization == "COGNITO_USER_POOLS"
    error_message = "get_result must require COGNITO_USER_POOLS: the route auth split is the flip's security boundary, and job results must not be readable anonymously now that the API is public."
  }

  assert {
    condition     = aws_api_gateway_method.this["get_result"].authorizer_id != null
    error_message = "get_result must be wired to the Cognito authorizer: the route auth split is the flip's security boundary, and a null authorizer_id here means the check never actually runs."
  }

  assert {
    condition     = aws_api_gateway_method.this["post_cancel"].authorization == "COGNITO_USER_POOLS"
    error_message = "post_cancel must require COGNITO_USER_POOLS: the route auth split is the flip's security boundary, and job control must not go anonymous now that the API is public."
  }

  assert {
    condition     = aws_api_gateway_method.this["post_cancel"].authorizer_id != null
    error_message = "post_cancel must be wired to the Cognito authorizer: the route auth split is the flip's security boundary, and a null authorizer_id here means the check never actually runs."
  }

  assert {
    condition     = aws_api_gateway_authorizer.cognito.type == "COGNITO_USER_POOLS"
    error_message = "The shared authorizer must stay a COGNITO_USER_POOLS authorizer: the route auth split is the flip's security boundary, and every closed route's authorizer_id points at this one resource."
  }

  assert {
    condition     = contains(aws_api_gateway_authorizer.cognito.provider_arns, var.cognito_user_pool_arn)
    error_message = "The authorizer must trust the configured Cognito user pool: the route auth split is the flip's security boundary, and drifted provider_arns would silently stop validating the tokens callers actually present."
  }

  # Nothing above pins WHERE the six data-route resources live in the
  # resource tree — only that the routes wired to them carry the right auth.
  # A stray re-parent (e.g. future /rmf work restructuring the tree) could
  # silently move a data route while every assert above stayed green.
  assert {
    condition = (
      aws_api_gateway_resource.config.parent_id == aws_api_gateway_rest_api.this.root_resource_id &&
      aws_api_gateway_resource.uploads.parent_id == aws_api_gateway_rest_api.this.root_resource_id &&
      aws_api_gateway_resource.jobs.parent_id == aws_api_gateway_rest_api.this.root_resource_id
    )
    error_message = "config, uploads, and jobs must stay parented directly at the API root: this is the data-route shape the spec pins, and nothing else in this file asserts it."
  }

  assert {
    condition     = aws_api_gateway_resource.job.parent_id == aws_api_gateway_resource.jobs.id
    error_message = "{job_id} must stay nested under jobs (/jobs/{job_id}): nothing else in this file pins this parent-child relationship."
  }

  assert {
    condition     = aws_api_gateway_resource.job_result.parent_id == aws_api_gateway_resource.job.id
    error_message = "result must stay nested under {job_id} (/jobs/{job_id}/result): nothing else in this file pins this parent-child relationship."
  }

  assert {
    condition     = aws_api_gateway_resource.job_cancel.parent_id == aws_api_gateway_resource.job.id
    error_message = "cancel must stay nested under {job_id} (/jobs/{job_id}/cancel): nothing else in this file pins this parent-child relationship."
  }

  assert {
    condition     = aws_api_gateway_method.spa_proxy[0].authorization == "NONE"
    error_message = "The stig SPA's asset route must stay open (NONE): the route auth split is the flip's security boundary, and the login shell itself has to load before a user can authenticate at all."
  }

  assert {
    condition     = aws_api_gateway_resource.spa_proxy[0].parent_id == aws_api_gateway_resource.stig[0].id
    error_message = "The SPA asset proxy must be nested under /stig: the bundle moved off the bare stage URL, and assets are requested relative to /stig/ (base: './'), not the API root."
  }

  assert {
    condition     = aws_api_gateway_method.stig[0].authorization == "NONE"
    error_message = "The /stig shell route must stay open (NONE): the route auth split is the flip's security boundary, and the stig SPA shell has to load before a user can authenticate at all."
  }

  assert {
    condition     = endswith(aws_api_gateway_integration.stig[0].uri, "/${aws_s3_bucket.spa[0].bucket}/index.html")
    error_message = "The /stig integration must proxy to <bucket>/index.html specifically: a bare endswith(uri, \"/index.html\") check also passes for .../landing/index.html (both keys share that suffix), so this has to pin the bucket-root object, not just the filename."
  }

  assert {
    condition     = aws_api_gateway_resource.stig[0].path_part == "stig"
    error_message = "The /stig resource's path_part must stay exactly \"stig\": the landing page links to ./stig/index.html and deploy.sh's smoke test hits /stig/ literally — renaming this resource would break both while every other assert in this file stayed green."
  }

  assert {
    condition     = aws_api_gateway_method.spa_root[0].authorization == "NONE"
    error_message = "The bare stage URL's landing route must stay open (NONE): the route auth split is the flip's security boundary, and the toolbox front door has to load before a user has signed in or chosen a tool."
  }

  assert {
    condition     = endswith(aws_api_gateway_integration.spa_root[0].uri, "/landing/index.html")
    error_message = "The root integration must proxy to the landing page: the bare stage URL is now the toolbox front door, not the stig SPA shell, and it must resolve to the landing object, not some other path in the bucket."
  }

  # --- /rmf (Phase R): the second tool's SPA shell + asset proxy -------------

  assert {
    condition     = aws_api_gateway_method.rmf[0].authorization == "NONE"
    error_message = "The /rmf shell route must stay open (NONE): the rmf SPA shell has to load before a user can authenticate at all, the same as /stig."
  }

  assert {
    condition     = aws_api_gateway_resource.rmf[0].path_part == "rmf"
    error_message = "The /rmf resource's path_part must stay exactly \"rmf\": the landing page links to ./rmf/index.html and deploy.sh's smoke test hits /rmf/ literally."
  }

  assert {
    condition     = endswith(aws_api_gateway_integration.rmf[0].uri, "/${aws_s3_bucket.spa[0].bucket}/rmf/index.html")
    error_message = "The /rmf integration must proxy to <bucket>/rmf/index.html specifically — the key deploy.sh's rmf SPA sync publishes to, distinct from the stig bundle at the bucket root and the landing page under landing/."
  }

  assert {
    condition     = aws_api_gateway_method.rmf_proxy[0].authorization == "NONE"
    error_message = "The rmf SPA's asset route must stay open (NONE): the login shell itself has to load before a user can authenticate at all."
  }

  assert {
    condition     = aws_api_gateway_resource.rmf_proxy[0].parent_id == aws_api_gateway_resource.rmf[0].id
    error_message = "The rmf asset proxy must be nested under /rmf: the bundle is built with VITE_BASE_PATH=/rmf/, so assets are requested relative to /rmf/, not the API root."
  }

  assert {
    condition     = endswith(aws_api_gateway_integration.rmf_proxy[0].uri, "/${aws_s3_bucket.spa[0].bucket}/rmf/{proxy}")
    error_message = "The rmf asset proxy must template the S3 key as <bucket>/rmf/{proxy}, mirroring spa_proxy's <bucket>/{proxy} one level down."
  }

  # --- /rmf/api (Phase R): HTTP_PROXY straight through to rmf's own API -----
  # /rmf/api (literal) and /rmf/{proxy+} (greedy, asserted above as rmf_proxy)
  # are siblings under /rmf. API Gateway prefers a literal path part over a
  # greedy path-parameter sibling, so /rmf/api/x must resolve into the proxy
  # below, never into the S3 read above — the parent_id asserts here pin the
  # sibling shape that reasoning depends on.

  assert {
    condition     = aws_api_gateway_resource.rmf_api[0].parent_id == aws_api_gateway_resource.rmf[0].id
    error_message = "/rmf/api must be a direct child of /rmf, a literal sibling of the {proxy+} asset route (rmf_proxy) — this sibling relationship is exactly what makes API Gateway's literal-over-greedy precedence apply."
  }

  assert {
    condition     = aws_api_gateway_method.rmf_api[0].authorization == "NONE"
    error_message = "The /rmf/api route must carry NONE at this gateway: rmf's own HTTP API enforces its own Cognito JWT authorizer on every route it defines, so a second authorizer here would be a redundant, drift-prone copy of that decision."
  }

  assert {
    condition     = aws_api_gateway_method.rmf_api[0].http_method == "ANY"
    error_message = "/rmf/api must accept ANY method: rmf's API surface includes GET/POST/PUT/DELETE, and this hop is a transparent proxy, not a route-specific integration."
  }

  assert {
    condition     = aws_api_gateway_integration.rmf_api[0].type == "HTTP_PROXY"
    error_message = "/rmf/api must use an HTTP_PROXY integration: it forwards to rmf's independently-hosted HTTP API, not to this API's own Lambda (AWS_PROXY) or the SPA bucket (AWS/S3)."
  }

  assert {
    condition     = aws_api_gateway_integration.rmf_api[0].uri == var.rmf_api_url
    error_message = "The literal /rmf/api integration must target rmf's bare API URL with no {proxy} suffix — there is no path parameter on this resource to interpolate one from."
  }

  assert {
    condition     = aws_api_gateway_resource.rmf_api_proxy[0].parent_id == aws_api_gateway_resource.rmf_api[0].id
    error_message = "/rmf/api/{proxy+} must be nested under /rmf/api, not directly under /rmf — it is api's greedy child, not rmf's."
  }

  assert {
    condition     = aws_api_gateway_method.rmf_api_proxy[0].authorization == "NONE"
    error_message = "The /rmf/api/{proxy+} route must carry NONE at this gateway: rmf's own HTTP API enforces its own Cognito JWT authorizer on every route it defines (verified live by deploy.sh's smoke test expecting 401, not 403/500, on a bare call)."
  }

  assert {
    condition     = aws_api_gateway_integration.rmf_api_proxy[0].type == "HTTP_PROXY"
    error_message = "/rmf/api/{proxy+} must use an HTTP_PROXY integration: it forwards every rmf route to rmf's independently-hosted HTTP API unmodified."
  }

  assert {
    condition     = endswith(aws_api_gateway_integration.rmf_api_proxy[0].uri, "/{proxy}")
    error_message = "The /rmf/api/{proxy+} integration uri must end with /{proxy} so the greedy path segment is forwarded to rmf's API; a bare var.rmf_api_url here (no {proxy}) would proxy every rmf route to the same fixed path."
  }

  assert {
    condition     = aws_api_gateway_integration.rmf_api_proxy[0].request_parameters["integration.request.path.proxy"] == "method.request.path.proxy"
    error_message = "The /rmf/api/{proxy+} integration must map integration.request.path.proxy from method.request.path.proxy — without it the {proxy} template in the uri never gets filled in from the incoming request path."
  }
}

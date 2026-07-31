# Every value below either has a safe default or must come from
# terraform.tfvars — see terraform.tfvars.example.

variable "name_prefix" {
  description = "Prefix for every resource name. Passed straight through to the module (see its own validation)."
  type        = string
}

variable "aws_region" {
  description = "GovCloud region for the stack."
  type        = string
  default     = "us-gov-west-1"
}

# --- Compute -------------------------------------------------------------

variable "bedrock_model_id" {
  description = "Bedrock model id this deployment invokes. No default: unlike stig-parser's enrichment bolt-on, rmf-migrator's drafting/chat features are core, not optional, so the module itself requires a real value — this variable mirrors that rather than adding an env-root-level off switch."
  type        = string
}

variable "lambda_zip_path" {
  description = "Path to the Lambda deployment zip built by scripts/build_lambda.py (apps/rmf-migrator/scripts/build_lambda.py), run under whichever interpreter satisfies backend/pyproject.toml's requires-python — deploy.sh tries six candidates (py -3.13, py -3.12, python3.13, python3.12, python3, python) rather than hard-requiring py -3.13. No default: filebase64sha256 fails at plan time without a real file."
  type        = string
}

# --- Auth (D2, shared toolbox login) --------------------------------------

variable "cognito_ssm_prefix" {
  description = "SSM path prefix the platform root publishes the shared pool under."
  type        = string
  default     = "/cyber-toolbox/dev"
}

variable "cognito_app_client_name" {
  description = <<-EOT
    This app's client in the shared pool. Defaults to stig-parser-web, not an
    rmf-specific name (D2): the toolbox uses ONE Cognito app client shared by
    every app, not one client per app, so a single login works across the
    whole toolbox. Minting an rmf-only client here would let a stig-parser
    login be rejected calling the rmf API (or vice versa), since each HTTP
    API's JWT authorizer checks the token's audience against exactly one
    configured client id. Override only if a future decision mints a second
    shared client.
  EOT
  type        = string
  default     = "stig-parser-web"
}

# --- Network / CORS ---------------------------------------------------------

variable "frame_ancestors" {
  description = <<-EOT
    Browser origin(s) allowed to call this API and to receive presigned S3
    uploads directly — the CORS allowlist for both. REQUIRED: the module
    fails the plan when this is empty; CORS never falls back to a wildcard.
    Set to the toolbox front door's own origin — scheme + host only, no
    path, no trailing slash (e.g.
    "https://abc123.execute-api.us-gov-west-1.amazonaws.com", never
    ".../v1" or ".../v1/").
  EOT
  type        = list(string)
  default     = []
}

variable "alert_email" {
  description = "Email address notified when a background job dead-letters (see the module's alert_email). Null disables the subscription; the alarm and SNS topic still exist."
  type        = string
  default     = null
}

# --- Tagging ---------------------------------------------------------------

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}

variable "name_prefix" {
  description = "Prefix applied to every resource name so this module can coexist with other tools in a shared account (e.g. \"rmf-migrator\" or \"toolbox-rmf\")."
  type        = string
  default     = "rmf-migrator"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.name_prefix))
    error_message = "name_prefix must be lowercase alphanumeric/hyphen, 2-31 chars, starting with a letter."
  }
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default     = {}
}

# ---- Network -----------------------------------------------------------------

variable "network_mode" {
  description = "\"private\" (default) exposes no public endpoints; the app is reached over the adopter's own network (VPN/portal). \"public\" fronts the API/SPA publicly for demos/dev. GovCloud production should stay \"private\"."
  type        = string
  default     = "private"

  validation {
    condition     = contains(["private", "public"], var.network_mode)
    error_message = "network_mode must be \"private\" or \"public\"."
  }
}

variable "vpc_id" {
  description = "Existing VPC ID. Required when network_mode = \"private\" (Lambdas run in-VPC and reach Bedrock/S3/DynamoDB via endpoints). Consumed, not created, so this module drops into an existing toolbox network."
  type        = string
  default     = null
}

variable "private_subnet_ids" {
  description = "Existing private subnet IDs for in-VPC Lambdas. Required when network_mode = \"private\"."
  type        = list(string)
  default     = []
}

variable "frame_ancestors" {
  description = "Origins allowed to embed the SPA in an iframe (CSP frame-ancestors), e.g. your internal tool portal. Empty list = same-origin only."
  type        = list(string)
  default     = []
}

# NOTE: the SPA's base path (for serving under a portal sub-path such as
# /rmf-migrator/) is a frontend build-time setting — set VITE_BASE_PATH when
# building the bundle. It is deliberately not a Terraform variable, because this
# module does not serve the SPA.

# ---- Encryption --------------------------------------------------------------

variable "kms_key_arn" {
  description = "ARN of an existing customer-managed KMS key to use for all encryption. Leave null to have this module create and manage one."
  type        = string
  default     = null
}

# ---- Bedrock -----------------------------------------------------------------

variable "bedrock_model_id" {
  description = "Bedrock model ID the tool invokes. Pure configuration — set to whatever your account has enabled. No default, to force a conscious choice."
  type        = string
}

variable "bedrock_region" {
  description = "Region for Bedrock calls. Defaults to the deployment region."
  type        = string
  default     = null
}

variable "bedrock_guardrail_id" {
  description = "Optional Bedrock Guardrail ID applied to prompts (prompt-injection defense). Leave null where Guardrails are unavailable (e.g. some GovCloud regions); prompt hardening is the fallback."
  type        = string
  default     = null
}

variable "bedrock_guardrail_version" {
  description = "Version of the Bedrock Guardrail, required if bedrock_guardrail_id is set."
  type        = string
  default     = null
}

# ---- Identity ----------------------------------------------------------------

variable "identity_header" {
  description = "Name of the trusted HTTP header carrying the caller's identity, injected by an upstream portal/proxy (e.g. \"X-Remote-User\"). Recorded in decision logs. Leave null to attribute all actions to \"anonymous\"."
  type        = string
  default     = null
}

# ---- Compute -----------------------------------------------------------------

variable "lambda_zip_path" {
  description = "Path to the packaged Lambda deployment zip (built by `make build` in backend/, or downloaded from a release)."
  type        = string
}

variable "lambda_runtime" {
  description = "Python runtime for Lambdas."
  type        = string
  default     = "python3.12"
}

variable "worker_timeout_seconds" {
  description = "Timeout for the parse/draft worker Lambda. Long LLM jobs need headroom; max is 900."
  type        = number
  default     = 900

  validation {
    condition     = var.worker_timeout_seconds > 0 && var.worker_timeout_seconds <= 900
    error_message = "worker_timeout_seconds must be between 1 and 900."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention. Logs contain metadata only (never document content), but retention is still bounded by default."
  type        = number
  default     = 30
}

variable "noncurrent_version_expiration_days" {
  description = "Days before a superseded (noncurrent) document version is expired from S3. Versioning protects against accidental overwrite; this stops old CUI versions accumulating forever."
  type        = number
  default     = 90
}

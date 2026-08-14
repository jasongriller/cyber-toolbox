variable "region" {
  description = "AWS region. Use us-gov-west-1 for GovCloud."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
  default     = "rmf-migrator"
}

variable "network_mode" {
  description = "\"private\" for production (default). \"public\" skips the VPC and requires an explicit auth_mode."
  type        = string
  default     = "private"

  validation {
    condition     = contains(["public", "private"], var.network_mode)
    error_message = "network_mode must be \"public\" or \"private\"."
  }
}

variable "auth_mode" {
  description = "API authorization: \"iam\", \"cognito\", or \"none\". Leave null in private mode (resolves to \"iam\"). Public mode requires an explicit choice; \"none\" exposes an UNAUTHENTICATED API and is for dev/demo only — never for CUI."
  type        = string
  default     = null

  validation {
    condition     = var.auth_mode == null || contains(["iam", "none", "cognito"], var.auth_mode)
    error_message = "auth_mode must be \"iam\", \"none\", or \"cognito\"."
  }
}

variable "bedrock_model_id" {
  description = "Bedrock model ID to invoke (must be enabled in your account/region)."
  type        = string
}

variable "lambda_zip_path" {
  description = "Path to the packaged Lambda zip (backend `make build`, or a release artifact)."
  type        = string
}

variable "vpc_id" {
  description = "Existing VPC ID (private mode only)."
  type        = string
  default     = null
}

variable "private_subnet_ids" {
  description = "Existing private subnet IDs (private mode only)."
  type        = list(string)
  default     = []
}

variable "identity_header" {
  description = "Trusted identity header injected by an upstream portal/proxy (optional)."
  type        = string
  default     = null
}

variable "frame_ancestors" {
  description = "Trusted browser origins for API/S3 CORS and the SPA CSP (required in private mode)."
  type        = list(string)
  default     = []
}

variable "alert_email" {
  description = "Email notified when a background job dead-letters (optional)."
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}

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
  description = "\"private\" for production (default). \"public\" exposes an UNAUTHENTICATED API and is for dev/demo only — never for CUI."
  type        = string
  default     = "private"

  validation {
    condition     = contains(["public", "private"], var.network_mode)
    error_message = "network_mode must be \"public\" or \"private\"."
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
  description = "Origins allowed to embed the SPA (optional)."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}

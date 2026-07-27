variable "name_prefix" {
  description = "Prefix for shared platform resources (e.g. \"cyber-toolbox-dev\"). The pool is named <prefix>-users."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.name_prefix))
    error_message = "name_prefix must be lowercase alphanumeric/hyphens, 2-31 chars."
  }
}

variable "ssm_prefix" {
  description = "SSM path prefix the pool/client ids are published under (e.g. \"/cyber-toolbox/dev\"). Apps consume these instead of reading this root's state."
  type        = string
  validation {
    condition     = can(regex("^/", var.ssm_prefix)) && !endswith(var.ssm_prefix, "/")
    error_message = "ssm_prefix must start with / and not end with /."
  }
}

variable "app_client_names" {
  description = "One SPA client per app (e.g. [\"stig-parser-web\"]). Adding an app later is a new entry here — no other change."
  type        = list(string)
  validation {
    condition     = length(var.app_client_names) > 0
    error_message = "At least one app client is required."
  }
}

variable "mfa_configuration" {
  description = "Pool MFA mode. ON per the 2026 decision (DoD bars single-factor for CUI); OFF only if the manager explicitly directs ssg-star parity."
  type        = string
  default     = "ON"
  validation {
    condition     = contains(["ON", "OPTIONAL", "OFF"], var.mfa_configuration)
    error_message = "mfa_configuration must be ON, OPTIONAL, or OFF."
  }
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}

variable "aws_region" {
  description = "Deployment region."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for shared platform resources. Pool becomes <prefix>-users."
  type        = string
  default     = "toolbox-dev"
}

variable "ssm_prefix" {
  description = "SSM path the pool/client ids are published under. Apps' env roots read these paths."
  type        = string
  default     = "/toolbox/dev"
}

variable "app_client_names" {
  description = "One SPA client per app. rmf-migrator-web joins this list when that app stands up."
  type        = list(string)
  default     = ["stig-parser-web"]
}

variable "mfa_configuration" {
  description = "Pool MFA mode — ON per the standing decision; see platform/README.md."
  type        = string
  default     = "ON"
}

variable "tags" {
  description = "Tags applied to every resource via provider default_tags."
  type        = map(string)
  default     = {}
}

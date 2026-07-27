variable "aws_region" {
  description = "Deployment region."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for shared platform resources. Pool becomes <prefix>-users."
  type        = string
  default     = "cyber-toolbox-dev"
}

variable "ssm_prefix" {
  description = "SSM path the pool/client ids are published under. Apps' env roots read these paths."
  type        = string
  default     = "/cyber-toolbox/dev"
}

variable "app_client_names" {
  description = "One SPA client per app. rmf-migrator-web joins this list when that app stands up."
  type        = list(string)
  default     = ["stig-parser-web"]
}

variable "mfa_configuration" {
  description = "Pool MFA mode. OFF by owner decision 2026-07-27 (password-only). ON adds an authenticator-app factor; the SPA already carries the enrollment and challenge screens, so flipping this back needs no code change."
  type        = string
  default     = "OFF"
}

variable "tags" {
  description = "Tags applied to every resource via provider default_tags."
  type        = map(string)
  default     = {}
}

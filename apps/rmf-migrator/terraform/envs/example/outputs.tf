output "rmf_api_url" {
  description = "Base URL of the rmf-migrator HTTP API. Also published to SSM at var.cognito_ssm_prefix + \"/rmf_api_url\" so the toolbox front door can read it without touching this root's state."
  value       = module.rmf_migrator.api_endpoint
}

output "rmf_api_url_ssm_parameter" {
  description = "SSM parameter name the api url was published under, for a deploy script to confirm against."
  value       = aws_ssm_parameter.rmf_api_url.name
}

output "documents_bucket" {
  description = "S3 bucket holding uploaded documents."
  value       = module.rmf_migrator.documents_bucket
}

output "table_name" {
  description = "DynamoDB metadata table."
  value       = module.rmf_migrator.table_name
}

output "parse_queue_url" {
  description = "Parse/draft job queue URL."
  value       = module.rmf_migrator.parse_queue_url
}

output "parse_dlq_url" {
  description = "Dead-letter queue URL for failed parse jobs."
  value       = module.rmf_migrator.parse_dlq_url
}

output "kms_key_arn" {
  description = "CMK protecting data at rest (module-created, since no kms_key_arn input is supplied)."
  value       = module.rmf_migrator.kms_key_arn
}

output "cognito_user_pool_id" {
  description = "Shared Cognito pool id this deployment authorizes against."
  value       = nonsensitive(data.aws_ssm_parameter.cognito_user_pool_id.value)
}

output "cognito_client_id" {
  description = "Shared Cognito app client id this deployment authorizes against."
  value       = nonsensitive(data.aws_ssm_parameter.cognito_client_id.value)
}

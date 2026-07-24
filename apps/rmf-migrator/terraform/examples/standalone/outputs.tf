output "api_endpoint" {
  description = "Base URL of the deployed HTTP API."
  value       = module.rmf_migrator.api_endpoint
}

output "api_execution_arn" {
  description = "Execution ARN for trusted caller IAM policies."
  value       = module.rmf_migrator.api_execution_arn
}

output "documents_bucket" {
  value = module.rmf_migrator.documents_bucket
}

output "table_name" {
  value = module.rmf_migrator.table_name
}

output "worker_function_name" {
  value = module.rmf_migrator.worker_function_name
}

output "kms_key_arn" {
  value = module.rmf_migrator.kms_key_arn
}

output "spa_csp_frame_ancestors" {
  description = "Set this as the Content-Security-Policy header value on the server that hosts the SPA."
  value       = module.rmf_migrator.spa_csp_frame_ancestors
}

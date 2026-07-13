output "api_endpoint" {
  description = "Base URL of the deployed HTTP API."
  value       = module.rmf_migrator.api_endpoint
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

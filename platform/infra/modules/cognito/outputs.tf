output "user_pool_id" {
  description = "Shared pool id (also published to SSM)."
  value       = aws_cognito_user_pool.this.id
}

output "user_pool_arn" {
  description = "Shared pool ARN (also published to SSM)."
  value       = aws_cognito_user_pool.this.arn
}

output "client_ids" {
  description = "App client ids by client name (also published to SSM)."
  value       = { for name, c in aws_cognito_user_pool_client.app : name => c.id }
}

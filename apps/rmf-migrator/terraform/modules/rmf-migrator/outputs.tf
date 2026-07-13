output "api_endpoint" {
  description = "Base URL of the HTTP API. In private mode this is reached only from inside the adopter's network."
  value       = aws_apigatewayv2_api.this.api_endpoint
}

output "documents_bucket" {
  description = "Name of the S3 bucket holding uploaded documents."
  value       = aws_s3_bucket.documents.id
}

output "table_name" {
  description = "Name of the DynamoDB metadata table."
  value       = aws_dynamodb_table.this.name
}

output "parse_queue_url" {
  description = "URL of the parse/draft job queue."
  value       = aws_sqs_queue.parse.id
}

output "parse_dlq_url" {
  description = "URL of the dead-letter queue for failed parse jobs."
  value       = aws_sqs_queue.parse_dlq.id
}

output "kms_key_arn" {
  description = "ARN of the CMK protecting data at rest (created or supplied)."
  value       = local.kms_key_arn
}

output "worker_function_name" {
  description = "Name of the parse/draft worker Lambda."
  value       = aws_lambda_function.worker.function_name
}

output "lambda_security_group_id" {
  description = "Security group ID for in-VPC Lambdas (private mode only; null otherwise)."
  value       = local.is_private ? aws_security_group.lambda[0].id : null
}

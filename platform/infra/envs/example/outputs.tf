# Consumed by deploy scripts (terraform output -raw) and humans. Apps read the
# SSM parameters instead — these outputs are convenience, not the contract.
output "cognito_user_pool_id" {
  value = module.cognito.user_pool_id
}

output "cognito_user_pool_arn" {
  value = module.cognito.user_pool_arn
}

output "cognito_client_ids" {
  value = module.cognito.client_ids
}

# Customer-managed KMS key for all data at rest (S3, DynamoDB, SQS, Lambda env,
# CloudWatch Logs). Created only when the caller did not supply an existing key.

resource "aws_kms_key" "this" {
  count = var.kms_key_arn == null ? 1 : 0

  description             = "${local.name} CMK for CUI at rest"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  tags                    = local.common_tags

  # Allow CloudWatch Logs to use the key for encrypting log groups.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRoot"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowCloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${local.region}.amazonaws.com" }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.name}-*"
          }
        }
      },
    ]
  })
}

resource "aws_kms_alias" "this" {
  count = var.kms_key_arn == null ? 1 : 0

  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.this[0].key_id
}

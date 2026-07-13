# Single-table metadata store (projects, documents, sections, jobs; later:
# mappings, decision-log entries). PK/SK schema matches backend/repository.py.
# CMK-encrypted, point-in-time recovery on.

resource "aws_dynamodb_table" "this" {
  name         = "${local.name}-table"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = local.kms_key_arn
  }

  point_in_time_recovery {
    enabled = true
  }

  deletion_protection_enabled = true

  tags = local.common_tags
}

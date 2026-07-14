# Document storage. CUI lives here — CMK-encrypted, versioned, fully private,
# TLS-only. Uploads arrive via presigned PUT from the browser; nothing is public.

resource "aws_s3_bucket" "documents" {
  # checkov:skip=CKV_AWS_18: Server access logging is intentionally not created
  # here. It requires a second bucket, and adopters deploying into an existing
  # boundary already have a central logging bucket with their own retention and
  # access policy. Point it at this bucket via your own
  # aws_s3_bucket_logging resource. (CloudTrail data events are the usual
  # requirement for CUI object-access auditing.)
  # checkov:skip=CKV_AWS_144: Cross-region replication is deliberately absent.
  # This bucket holds CUI; silently copying it into a second region would work
  # against the data-residency posture GovCloud adopters need.
  # checkov:skip=CKV2_AWS_62: S3 event notifications are not used. Upload
  # completion is signalled explicitly by the client calling the parse endpoint,
  # so there is no event to subscribe to.
  bucket        = "${local.name}-documents-${local.account_id}"
  force_destroy = false
  tags          = local.common_tags
}

# Versioning is on, so old object versions would otherwise accumulate forever.
# Expire noncurrent versions and clean up aborted multipart uploads — CUI should
# not linger past its usefulness.
resource "aws_s3_bucket_lifecycle_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.documents]
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket = aws_s3_bucket.documents.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = local.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

# CORS: allow the SPA origin to PUT directly to S3 via presigned URL. In private
# mode the SPA origin is internal; frame_ancestors/app origin is configured by
# the adopter. Kept permissive on method, pinned on headers used by presign.
resource "aws_s3_bucket_cors_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = length(var.frame_ancestors) > 0 ? var.frame_ancestors : ["*"]
    allowed_headers = [
      "content-type",
      "x-amz-server-side-encryption",
      "x-amz-server-side-encryption-aws-kms-key-id",
    ]
    max_age_seconds = 3000
  }
}

# Deny any non-TLS access.
resource "aws_s3_bucket_policy" "documents" {
  bucket = aws_s3_bucket.documents.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.documents.arn,
          "${aws_s3_bucket.documents.arn}/*",
        ]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })
}

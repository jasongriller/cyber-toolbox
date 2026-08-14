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

  # Deliberately outside var.enable_doc_conversion, and deliberately a rule in
  # this resource rather than a second aws_s3_bucket_lifecycle_configuration —
  # that API replaces a bucket's entire lifecycle configuration, so two
  # resources on one bucket would take turns deleting each other's rules.
  #
  # convert-scratch/ holds whole CUI documents: the staged .doc and the
  # converted .docx. The converter purges the prefix itself, but a purge can
  # fail (logged, non-fatal), a timed-out conversion can write its output after
  # the caller gave up, and delete_project purges only projects/<id>/ — so
  # without this rule an authorized project deletion reports success while a
  # complete copy of the source document survives. This is the one mitigation
  # that still holds when the grants or the alarm are misconfigured, which is
  # why it exists whether or not conversion is switched on.
  rule {
    id     = "expire-convert-scratch"
    status = "Enabled"

    filter {
      prefix = "convert-scratch/"
    }

    expiration {
      days = 1
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
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

# CORS: allow only configured SPA origins to PUT via presigned URL. Every
# posture requires an explicit allowlist (validate_cors_origins); there is no
# wildcard fallback. Methods and headers stay pinned to the presigned upload.
resource "aws_s3_bucket_cors_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  cors_rule {
    # POST: presigned-POST uploads (policy carries the size ceiling). PUT is
    # kept through the transition for browsers still running the previous
    # SPA build; drop it once every deployment is past the POST switch.
    allowed_methods = ["POST", "PUT"]
    allowed_origins = var.frame_ancestors
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

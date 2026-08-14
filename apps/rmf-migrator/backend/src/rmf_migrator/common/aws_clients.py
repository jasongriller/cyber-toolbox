"""Shared botocore client configuration.

Every internally-built AWS client pins explicit connect/read timeouts sized
under the Lambdas' own timeouts. The botocore defaults (60s connect / 60s
read, legacy retries) can hold a handler until Lambda hard-kills it mid-write,
skipping every error path — the job tables' stale-lease reclaim covers job
records, but plain put_item/send_message calls have no such net.
"""

from __future__ import annotations

from botocore.config import Config as BotoConfig

# Metadata-plane calls (DynamoDB, SQS, S3): small payloads, should fail fast
# and surface as a logged error instead of riding out the Lambda timeout.
FAST_CONFIG = BotoConfig(
    connect_timeout=5,
    read_timeout=30,
    retries={"max_attempts": 3, "mode": "standard"},
)

# Bedrock converse calls legitimately run for minutes on long drafting jobs
# (worker timeout is up to 900s); only the connect must fail fast. Retries are
# left at 2 so a slow-but-alive generation is not billed twice.
BEDROCK_CONFIG = BotoConfig(
    connect_timeout=5,
    read_timeout=600,
    retries={"max_attempts": 2, "mode": "standard"},
)

# Synchronous invoke of the LibreOffice converter. The converter's own timeout is
# owned by var.converter_timeout_seconds on aws_lambda_function.converter
# (terraform/modules/rmf-migrator/converter.tf), whose validation block refuses a
# value that reaches the read timeout below (default 120s). Mirrored on the
# Python side by _CONVERTER_FUNCTION_TIMEOUT in tests/test_doc_convert.py, since
# the two are not otherwise wired together. Retries must stay at 1: Lambda does
# not cancel a RequestResponse invocation when the client gives up, so a retried conversion
# means a second 2 GB LibreOffice run writing to scratch keys the caller may
# already have purged.
#
# Stated as total_max_attempts, not max_attempts: botocore reads max_attempts as
# a *retry* count and resolves it to total_max_attempts = value + 1, so the
# obvious spelling of "never retry" buys two invocations. It also rewrites the
# dict on the shared constant in place, which would restate the pin for every
# later reader of this module.
CONVERT_CONFIG = BotoConfig(
    connect_timeout=5,
    read_timeout=150,
    retries={"total_max_attempts": 1, "mode": "standard"},
)

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

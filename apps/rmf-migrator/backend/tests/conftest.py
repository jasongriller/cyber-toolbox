"""Shared fixtures: a moto-backed Deps bundle so handlers run end-to-end offline."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from rmf_migrator.common.config import Config
from rmf_migrator.common.repository import Repository
from rmf_migrator.common.storage import DocumentStore
from rmf_migrator.handlers.deps import Deps

_REGION = "us-east-1"
_BUCKET = "test-documents"
_TABLE = "test-table"
_KMS_KEY = "alias/test-key"


@pytest.fixture
def aws():
    with mock_aws():
        yield


@pytest.fixture
def deps(aws) -> Deps:
    # DynamoDB single table (PK/SK).
    ddb = boto3.resource("dynamodb", region_name=_REGION)
    ddb.create_table(
        TableName=_TABLE,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # S3 bucket.
    s3 = boto3.client("s3", region_name=_REGION)
    s3.create_bucket(Bucket=_BUCKET)

    # SQS queue.
    sqs = boto3.client("sqs", region_name=_REGION)
    queue_url = sqs.create_queue(QueueName="test-parse-queue")["QueueUrl"]

    config = Config(
        documents_bucket=_BUCKET,
        table_name=_TABLE,
        kms_key_id=_KMS_KEY,
        parse_queue_url=queue_url,
        bedrock_model_id="test.model",
        bedrock_region=_REGION,
        identity_header="X-Remote-User",
        bedrock_guardrail_id=None,
        bedrock_guardrail_version=None,
    )
    return Deps(
        config=config,
        repo=Repository(_TABLE, dynamodb_resource=ddb),
        store=DocumentStore(_BUCKET, _KMS_KEY, s3_client=s3),
        sqs=sqs,
    )

"""Internally-built AWS clients must pin explicit timeouts.

The botocore defaults (60s connect / 60s read, legacy retries) can hold a
handler near its Lambda timeout on a bad network path and get it hard-killed
mid-write, skipping every error path. Only the S3 client pinned a Config (for
SigV4); the rest inherited defaults.
"""

from __future__ import annotations

import pytest

from rmf_migrator.common.bedrock import BedrockClient
from rmf_migrator.common.repository import Repository
from rmf_migrator.common.storage import DocumentStore


@pytest.fixture
def region_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


def test_dynamodb_resource_pins_fast_timeouts(region_env, aws):
    config = Repository("t")._table.meta.client.meta.config
    assert config.connect_timeout <= 5
    assert config.read_timeout <= 30


def test_s3_client_pins_fast_timeouts_and_keeps_sigv4(region_env):
    config = DocumentStore("b", "k")._s3.meta.config
    assert config.connect_timeout <= 5
    assert config.read_timeout <= 60
    assert config.signature_version == "s3v4"


def test_bedrock_client_allows_long_reads_but_fast_connects(region_env):
    config = BedrockClient("test.model", region="us-east-1")._client.meta.config
    assert config.connect_timeout <= 5
    # LLM generations are legitimately slow; only the connect must fail fast.
    assert config.read_timeout >= 300


def test_deps_sqs_client_pins_fast_timeouts(region_env, aws, monkeypatch):
    from rmf_migrator.common.config import get_config
    from rmf_migrator.handlers.deps import Deps

    for key, value in {
        "DOCUMENTS_BUCKET": "b",
        "TABLE_NAME": "t",
        "KMS_KEY_ID": "k",
        "PARSE_QUEUE_URL": "https://sqs.test/q",
        "BEDROCK_MODEL_ID": "test.model",
        "AWS_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(key, value)
    get_config.cache_clear()
    Deps.reset_cache()
    try:
        config = Deps.build().sqs.meta.config
        assert config.connect_timeout <= 5
        assert config.read_timeout <= 30
    finally:
        get_config.cache_clear()
        Deps.reset_cache()

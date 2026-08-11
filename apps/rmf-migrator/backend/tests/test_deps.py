"""Deps.build() must reuse its bundle across warm-container invocations.

Every handler calls Deps.build() per invocation. Rebuilding boto3 clients each
time defeats connection-pool reuse and adds avoidable latency on warm starts —
Config is lru_cached for exactly this reason, and the clients must follow.
"""

from __future__ import annotations

import pytest

from rmf_migrator.common.config import get_config
from rmf_migrator.doc_convert import RejectingConverter
from rmf_migrator.handlers.deps import Deps

_ENV = {
    "DOCUMENTS_BUCKET": "cache-test-bucket",
    "TABLE_NAME": "cache-test-table",
    "KMS_KEY_ID": "alias/cache-test",
    "PARSE_QUEUE_URL": "https://sqs.test/queue",
    "BEDROCK_MODEL_ID": "test.model",
    "AWS_REGION": "us-east-1",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
}


@pytest.fixture
def build_env(monkeypatch, aws):
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DOC_CONVERSION_BACKEND", raising=False)
    monkeypatch.delenv("DOC_CONVERTER_FUNCTION_NAME", raising=False)
    get_config.cache_clear()
    Deps.reset_cache()
    yield
    get_config.cache_clear()
    Deps.reset_cache()


def test_build_returns_same_bundle_on_warm_invocations(build_env):
    first = Deps.build()
    second = Deps.build()
    assert first is second
    assert first.sqs is second.sqs
    assert first.repo is second.repo
    assert first.store is second.store
    assert first.bedrock is second.bedrock
    assert first.converter is second.converter


def test_build_wires_the_default_converter(build_env):
    """converter defaults to None on the dataclass, so dropping the build()
    wiring is invisible until a worker calls deps.converter.convert()."""
    assert isinstance(Deps.build().converter, RejectingConverter)


def test_reset_cache_forces_a_fresh_bundle(build_env):
    first = Deps.build()
    Deps.reset_cache()
    assert Deps.build() is not first

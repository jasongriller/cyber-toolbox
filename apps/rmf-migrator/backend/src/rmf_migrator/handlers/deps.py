"""Dependency assembly for handlers.

Builds a ``Deps`` bundle from configuration and real AWS clients. Tests
construct ``Deps`` directly with fakes/moto-backed clients, so handler logic
never reaches for globals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3

from rmf_migrator.common.aws_clients import FAST_CONFIG
from rmf_migrator.common.bedrock import BedrockClient
from rmf_migrator.common.config import Config, get_config
from rmf_migrator.common.repository import Repository
from rmf_migrator.common.storage import DocumentStore


@dataclass
class Deps:
    config: Config
    repo: Repository
    store: DocumentStore
    sqs: Any
    # Bedrock is only needed by the mapping/drafting workers; API handlers leave
    # it None. Tests inject a fake.
    bedrock: Any = None

    @staticmethod
    def build() -> Deps:
        # Cached per warm Lambda container, like get_config: rebuilding boto3
        # clients on every invocation defeats connection-pool reuse.
        global _built
        if _built is None:
            config = get_config()
            _built = Deps(
                config=config,
                repo=Repository(config.table_name),
                store=DocumentStore(config.documents_bucket, config.kms_key_id),
                sqs=boto3.client("sqs", config=FAST_CONFIG),
                bedrock=BedrockClient.from_config(config),
            )
        return _built

    @staticmethod
    def reset_cache() -> None:
        """Drop the cached bundle (tests; pairs with get_config.cache_clear)."""
        global _built
        _built = None


_built: Deps | None = None

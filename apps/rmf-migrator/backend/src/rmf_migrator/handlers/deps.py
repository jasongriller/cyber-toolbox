"""Dependency assembly for handlers.

Builds a ``Deps`` bundle from configuration and real AWS clients. Tests
construct ``Deps`` directly with fakes/moto-backed clients, so handler logic
never reaches for globals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3

from rmf_migrator.common.config import Config, get_config
from rmf_migrator.common.repository import Repository
from rmf_migrator.common.storage import DocumentStore


@dataclass
class Deps:
    config: Config
    repo: Repository
    store: DocumentStore
    sqs: Any

    @staticmethod
    def build() -> Deps:
        config = get_config()
        return Deps(
            config=config,
            repo=Repository(config.table_name),
            store=DocumentStore(config.documents_bucket, config.kms_key_id),
            sqs=boto3.client("sqs"),
        )

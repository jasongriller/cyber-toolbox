"""Shared fixtures: a moto-backed Deps bundle so handlers run end-to-end offline."""

from __future__ import annotations

from dataclasses import replace

import boto3
import pytest
from moto import mock_aws

from rmf_migrator.common.config import Config
from rmf_migrator.common.models import Project
from rmf_migrator.common.repository import Repository
from rmf_migrator.common.storage import DocumentStore
from rmf_migrator.doc_convert import build_converter
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


@pytest.fixture
def deps_with_project(deps) -> tuple[Deps, str]:
    """``deps`` plus a stored project, for handlers that require one to exist."""
    project = Project(name="Test Project")
    deps.repo.put_project(project)
    return deps, project.project_id


@pytest.fixture
def deps_with_conversion(deps_with_project, monkeypatch) -> tuple[Deps, str]:
    """``deps_with_project`` with legacy .doc conversion switched on."""
    deps, project_id = deps_with_project
    # The lambda backend builds its own boto3 client, which needs a region.
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    deps.config = replace(
        deps.config,
        doc_conversion_backend="lambda",
        doc_converter_function_name="fn",
    )
    # Deps.build() derives the converter from the config, so a fixture that set
    # only the config would manufacture a config-says-lambda/converter-None
    # pairing that production can never produce.
    deps.converter = build_converter(deps.config, store=deps.store)
    return deps, project_id


class FakeBedrock:
    """Fake Bedrock client returning a fixed mapping result for every section."""

    def __init__(self, result=None):
        self.result = result or {
            "control_ids": ["AC-2"],
            "confidence": 0.8,
            "rationale": "account management",
        }

    def converse_json(self, *, system: str, user: str, max_tokens: int = 2048):
        return self.result


def sqs_event(*bodies: dict) -> dict:
    """Wrap message bodies into an SQS Lambda event."""
    import json

    return {
        "Records": [{"messageId": f"m{i}", "body": json.dumps(b)} for i, b in enumerate(bodies)]
    }


def ole2_directory(*streams: str | tuple[str, int]) -> bytes:
    """CFBF directory entries for a root storage plus `streams`.

    Only the fields the format sniff reads (name, name length, object type) are
    populated; the streams have no contents. A name given as a
    `(name, object_type)` pair sets that entry's object type, so a caller can
    build a storage (1) where a stream (2) would be expected. Padded to whole
    512-byte sectors, which hold four entries each.
    """
    entries = bytearray()
    for item in (("Root Entry", 5), *streams):  # root storage, then streams
        name, object_type = item if isinstance(item, tuple) else (item, 2)
        entry = bytearray(128)
        encoded = name.encode("utf-16-le")
        entry[: len(encoded)] = encoded
        entry[64:66] = (len(encoded) + 2).to_bytes(2, "little")
        entry[66] = object_type
        entries += entry
    return bytes(entries) + bytes(-len(entries) % 512)


def ole2_container(*streams: str | tuple[str, int]) -> bytes:
    """Build a minimal OLE2/CFBF container whose directory lists `streams`.

    Word .doc, Excel .xls, PowerPoint .ppt and .msi all share the CFBF
    signature, so a fixture that only carries the magic bytes cannot exercise
    the part of the sniff that tells them apart. Sectors are 512 bytes, so a
    fifth name spills past the first directory sector into the sector chain.
    """
    sector = 512
    directory = ole2_directory(*streams)
    dir_sectors = len(directory) // sector

    # Sector 0 holds the FAT; the directory chain runs from sector 1 onward.
    fat = bytearray(b"\xff" * sector)  # FREESECT
    fat[0:4] = (0xFFFFFFFD).to_bytes(4, "little")  # FATSECT
    for i in range(dir_sectors):
        following = 2 + i if i + 1 < dir_sectors else 0xFFFFFFFE  # ENDOFCHAIN
        fat[4 + i * 4 : 8 + i * 4] = following.to_bytes(4, "little")

    header = bytearray(sector)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    header[26:28] = (3).to_bytes(2, "little")  # major version
    header[28:30] = b"\xfe\xff"  # little-endian byte order
    header[30:32] = (9).to_bytes(2, "little")  # 512-byte sectors
    header[32:34] = (6).to_bytes(2, "little")  # 64-byte mini sectors
    header[44:48] = (1).to_bytes(4, "little")  # one FAT sector
    header[48:52] = (1).to_bytes(4, "little")  # directory starts at sector 1
    header[56:60] = (4096).to_bytes(4, "little")  # mini stream cutoff
    header[60:64] = (0xFFFFFFFE).to_bytes(4, "little")  # no mini FAT
    header[68:72] = (0xFFFFFFFE).to_bytes(4, "little")  # no DIFAT sectors
    header[76:80] = (0).to_bytes(4, "little")  # DIFAT[0] -> the FAT at sector 0
    header[80:512] = b"\xff" * 432  # remaining DIFAT slots are free
    return bytes(header) + bytes(fat) + bytes(directory)

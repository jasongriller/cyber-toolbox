"""LibreOffice conversion backend, invoked as a separate container-image Lambda.

Isolation is the point: LibreOffice parses a macro-bearing binary format with a
long CVE history, so it runs in its own function with its own minimal role,
never inside the API or parse workers.

That isolation does not include the network. A Lambda outside a VPC has full
internet egress and LibreOffice uses it — a linked graphic in a .doc is fetched
during conversion, measured in the built image. The image disables link updates
and remote loading; a VPC with no NAT or internet gateway is the network half,
required before this backend is enabled anywhere (Task 12).

Bytes travel via S3 scratch keys because a 15 MB .doc exceeds Lambda's 6 MB
synchronous invoke payload. Staging is an implementation detail of this backend
so the DocConverter port stays bytes-in/bytes-out.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from rmf_migrator.common.limits import (
    CONVERT_SCRATCH_PREFIX,
    MAX_DOC_BYTES,
    MAX_DOCX_BYTES,
    sniff_document_format,
)
from rmf_migrator.common.logging import log_error

from .base import ConversionError, ConversionFailed


class LibreOfficeLambdaConverter:
    def __init__(
        self,
        *,
        function_name: str,
        bucket: str,
        store: Any,
        lambda_client: Any = None,
    ) -> None:
        self._function_name = function_name
        self._bucket = bucket
        self._store = store
        if lambda_client is None:
            import copy

            import boto3

            from rmf_migrator.common.aws_clients import CONVERT_CONFIG

            # botocore rewrites the retries dict of the config it is handed
            # (max_attempts becomes total_max_attempts) in place, so passing
            # the module-level constant would leave the shared pin rewritten
            # for every later reader of it.
            lambda_client = boto3.client("lambda", config=copy.deepcopy(CONVERT_CONFIG))
        self._lambda = lambda_client

    def convert(self, data: bytes) -> bytes:
        if len(data) > MAX_DOC_BYTES:
            raise ConversionFailed(f"legacy .doc exceeds {MAX_DOC_BYTES} bytes")

        token = uuid.uuid4().hex
        # The same constant the converter checks against; the trailing slash
        # belongs to it, so the two ends cannot drift into a mismatch that
        # would surface as the converter refusing every key it is handed.
        scratch_prefix = f"{CONVERT_SCRATCH_PREFIX}{token}"
        source_key = f"{scratch_prefix}.doc"
        target_key = f"{scratch_prefix}.docx"
        try:
            # Transport and storage errors are convert-stage failures; letting
            # a ClientError or ObjectTooLarge escape would make the worker
            # record this as a parse failure.
            try:
                self._store.put_bytes(source_key, data, "application/msword")
                self._invoke(source_key, target_key)
                converted = self._store.get_bytes(target_key, max_bytes=MAX_DOCX_BYTES)
            except ConversionError:
                raise
            except Exception as exc:  # noqa: BLE001 — the port owns the contract
                raise ConversionFailed(f"conversion backend error: {type(exc).__name__}") from exc
        finally:
            # Scratch holds document content, so it goes on every path. The
            # bucket is versioned, so a plain delete would only write a delete
            # marker and leave the bytes as a noncurrent version that no purge
            # flow reaches; delete_prefix removes every version. The token is a
            # uuid, so the prefix covers these two keys and nothing else.
            try:
                self._store.delete_prefix(scratch_prefix)
            except Exception as exc:  # noqa: BLE001 — cleanup is best-effort
                # The prefix is a uuid token, not a document name. Silence here
                # would let a broken delete grant accumulate copies unreported.
                log_error("doc_convert.scratch_delete_failed", exc, prefix=scratch_prefix)

        if sniff_document_format(converted) != "docx":
            raise ConversionFailed("converter did not produce a .docx")
        return converted

    def _invoke(self, source_key: str, target_key: str) -> None:
        response = self._lambda.invoke(
            FunctionName=self._function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(
                {
                    "bucket": self._bucket,
                    "source_key": source_key,
                    "target_key": target_key,
                }
            ).encode(),
        )
        if response.get("FunctionError"):
            # The payload may carry document content in an error string; record
            # that it failed, never what it said.
            raise ConversionFailed("conversion function returned an error")
        if response.get("StatusCode") != 200:
            raise ConversionFailed(f"conversion function status {response.get('StatusCode')}")

"""S3 document storage helpers.

Uploads use presigned PUT URLs so document bytes flow browser -> S3 directly and
never transit a Lambda. Server-side encryption uses the project's KMS CMK; the
presigned URL pins the SSE headers so an upload that omits them is rejected.
"""

from __future__ import annotations

from typing import Any

import boto3

from rmf_migrator.common.limits import MAX_DOCX_BYTES, ObjectTooLarge

# Only .docx is accepted in v1.
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Presigned upload URLs are short-lived.
_UPLOAD_URL_TTL_SECONDS = 300


def build_document_key(project_id: str, document_id: str, filename: str) -> str:
    """Deterministic S3 key. Filename is stored in metadata, not the key, to
    avoid leaking potentially sensitive names into access logs / URLs."""
    return f"projects/{project_id}/documents/{document_id}.docx"


def build_export_key(project_id: str, document_id: str) -> str:
    """S3 key for the generated Rev 5 .docx."""
    return f"projects/{project_id}/exports/{document_id}-rev5.docx"


def build_section_text_key(project_id: str, document_id: str, section_id: str) -> str:
    """S3 key for a section body too large for a DynamoDB item."""
    return f"projects/{project_id}/sections/{document_id}/{section_id}.txt"


class DocumentStore:
    def __init__(self, bucket: str, kms_key_id: str, *, s3_client: Any = None) -> None:
        self._bucket = bucket
        self._kms_key_id = kms_key_id
        self._s3 = s3_client or boto3.client("s3")

    def presigned_put_url(self, key: str) -> dict[str, Any]:
        """Return a presigned PUT URL and the headers the caller must send.

        The SSE headers are part of the signed request, enforcing CMK
        encryption on upload.
        """
        params = {
            "Bucket": self._bucket,
            "Key": key,
            "ContentType": DOCX_CONTENT_TYPE,
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": self._kms_key_id,
        }
        url = self._s3.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=_UPLOAD_URL_TTL_SECONDS
        )
        return {
            "url": url,
            "method": "PUT",
            "headers": {
                "Content-Type": DOCX_CONTENT_TYPE,
                "x-amz-server-side-encryption": "aws:kms",
                "x-amz-server-side-encryption-aws-kms-key-id": self._kms_key_id,
            },
            "expires_in": _UPLOAD_URL_TTL_SECONDS,
        }

    def head(self, key: str) -> dict[str, Any]:
        """Return object metadata without downloading policy content."""
        return self._s3.head_object(Bucket=self._bucket, Key=key)

    def get_bytes(self, key: str, *, max_bytes: int | None = MAX_DOCX_BYTES) -> bytes:
        """Download an object, refusing anything over ``max_bytes``.

        The size is checked with HeadObject first so an oversized object is
        never pulled into the Lambda's memory at all. Nothing constrains the
        size of a presigned PUT, so this is where an oversized upload is caught.
        """
        if max_bytes is not None:
            head = self._s3.head_object(Bucket=self._bucket, Key=key)
            length = head.get("ContentLength")
            if length is not None and length > max_bytes:
                raise ObjectTooLarge(f"object {key} exceeds {max_bytes} bytes")
        resp = self._s3.get_object(Bucket=self._bucket, Key=key)
        body = resp["Body"]
        try:
            data = body.read() if max_bytes is None else body.read(max_bytes + 1)
        finally:
            body.close()
        if max_bytes is not None and len(data) > max_bytes:
            raise ObjectTooLarge(f"object {key} exceeds {max_bytes} bytes")
        return data

    def put_bytes(self, key: str, data: bytes, content_type: str = DOCX_CONTENT_TYPE) -> None:
        """Write bytes with CMK encryption (used for generated Rev 5 exports)."""
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=self._kms_key_id,
        )

    def presigned_get_url(self, key: str, *, download_name: str | None = None) -> dict[str, Any]:
        """Presigned GET URL so the browser downloads the export directly from S3."""
        params: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
        url = self._s3.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=_UPLOAD_URL_TTL_SECONDS
        )
        return {"url": url, "expires_in": _UPLOAD_URL_TTL_SECONDS}

    def delete_prefix(self, prefix: str) -> int:
        """Permanently delete every object version and marker under a prefix.

        Returns the number of objects deleted.
        """
        deleted = 0
        # Re-list the first page after every deletion. This stays bounded and
        # avoids advancing with continuation markers that point at versions we
        # just removed.
        while True:
            page = self._s3.list_object_versions(
                Bucket=self._bucket,
                Prefix=prefix,
                MaxKeys=1000,
            )
            objects = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for item in [*page.get("Versions", []), *page.get("DeleteMarkers", [])]
            ]
            if not objects:
                break
            response = self._s3.delete_objects(
                Bucket=self._bucket,
                Delete={"Objects": objects, "Quiet": True},
            )
            errors = response.get("Errors", [])
            if errors:
                raise RuntimeError(f"failed to purge {len(errors)} stored object versions")
            deleted += len(objects)
        return deleted

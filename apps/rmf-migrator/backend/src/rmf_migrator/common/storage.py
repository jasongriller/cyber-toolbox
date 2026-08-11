"""S3 document storage helpers.

Uploads use presigned POST targets so document bytes flow browser -> S3 directly and
never transit a Lambda. Server-side encryption uses the project's KMS CMK; the
presigned URL pins the SSE headers so an upload that omits them is rejected.
"""

from __future__ import annotations

from typing import Any, Literal

import boto3
from botocore.config import Config

from rmf_migrator.common.aws_clients import FAST_CONFIG
from rmf_migrator.common.limits import MAX_DOC_BYTES, MAX_DOCX_BYTES, ObjectTooLarge

# Only .docx is accepted in v1.
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Legacy Word 97-2003 binary; accepted only as a conversion source, never parsed directly.
DOC_CONTENT_TYPE = "application/msword"

# What each accepted source format may be uploaded as: (content type, size ceiling).
# One table rather than two parameters, because the pair is one decision — a
# ceiling that can be chosen separately from the type is a ceiling that can be
# chosen wrongly, and the loose one (MAX_DOCX_BYTES, sized for compressed XML)
# is the fail-open direction for uncompressed OLE2. The upload policy is the
# only place MAX_DOC_BYTES can be enforced before the bytes exist.
_UPLOAD_POLICIES: dict[str, tuple[str, int]] = {
    "docx": (DOCX_CONTENT_TYPE, MAX_DOCX_BYTES),
    "doc": (DOC_CONTENT_TYPE, MAX_DOC_BYTES),
}

# Presigned upload URLs are short-lived.
_UPLOAD_URL_TTL_SECONDS = 300


def content_disposition(download_name: str) -> str:
    """Build a safe ``attachment`` Content-Disposition value.

    Upload validation rejects header-breaking filenames, but stored names may
    predate that check, so this is enforced again at the only place the value
    reaches a header: control characters, quotes, and backslashes are stripped
    (they terminate or escape the quoted parameter), and non-ASCII names get an
    ASCII fallback plus an RFC 5987 ``filename*`` carrying the real name.
    """
    from urllib.parse import quote

    cleaned = "".join(c for c in download_name if c.isprintable() and c not in '"\\')
    fallback = cleaned.encode("ascii", "replace").decode("ascii").replace("?", "_")
    disposition = f'attachment; filename="{fallback}"'
    if cleaned != fallback:
        disposition += f"; filename*=UTF-8''{quote(cleaned, safe='')}"
    return disposition


def build_document_key(project_id: str, document_id: str, filename: str) -> str:
    """Deterministic S3 key. Filename is stored in metadata, not the key, to
    avoid leaking potentially sensitive names into access logs / URLs."""
    suffix = ".doc" if filename.lower().endswith(".doc") else ".docx"
    return f"projects/{project_id}/documents/{document_id}{suffix}"


def build_converted_document_key(project_id: str, document_id: str) -> str:
    """S3 key for the .docx produced from an uploaded legacy .doc.

    A separate key, never an overwrite: the original .doc is audit provenance
    for the A&A package and must remain byte-identical to what was uploaded.
    """
    return f"projects/{project_id}/documents/{document_id}.converted.docx"


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
        # SigV4 pinned: the default client in legacy-listed regions
        # (us-gov-west-1 included) presigns SigV2-form URLs, which buckets
        # created after 2020-06 reject with 403. Merged onto the shared
        # fast-timeout base (merge: the argument's values win).
        self._s3 = s3_client or boto3.client(
            "s3", config=FAST_CONFIG.merge(Config(signature_version="s3v4"))
        )

    def presigned_post(
        self,
        key: str,
        *,
        source_format: Literal["docx", "doc"] = "docx",
    ) -> dict[str, Any]:
        """Return a presigned POST target with an S3-side size ceiling.

        Presigned PUT cannot express a size limit, so an oversized object
        could land in the bucket before the app-side check rejected it (a
        storage-cost/DoS vector). A POST policy's content-length-range makes
        S3 itself refuse anything over the format's ceiling, and the pinned
        fields keep enforcing CMK encryption exactly as the PUT headers did.

        The format alone selects the content type and the ceiling together,
        and the key must carry that format's extension — `build_document_key`
        derives it, so a mismatch means the caller lost track of which
        document this target is for. Refusing is the fail-closed answer: the
        alternative is minting a 25 MB, docx-typed policy for a .doc key,
        which admits OLE2 bytes the converter would later refuse but which
        are by then already in the bucket.

        The pin binds the declared type, not the bytes — content-based
        rejection stays with the format sniff on download.
        """
        policy = _UPLOAD_POLICIES.get(source_format)
        if policy is None:
            raise ValueError(f"no upload policy for source format {source_format!r}")
        content_type, max_bytes = policy
        # The format names are the extensions, and ".docx" does not end in
        # ".doc", so this one check catches a mismatch in either direction.
        if not key.endswith(f".{source_format}"):
            raise ValueError(f"upload key {key!r} is not a .{source_format} key")

        post = self._s3.generate_presigned_post(
            Bucket=self._bucket,
            Key=key,
            Fields={
                "Content-Type": content_type,
                "x-amz-server-side-encryption": "aws:kms",
                "x-amz-server-side-encryption-aws-kms-key-id": self._kms_key_id,
            },
            Conditions=[
                {"Content-Type": content_type},
                {"x-amz-server-side-encryption": "aws:kms"},
                {"x-amz-server-side-encryption-aws-kms-key-id": self._kms_key_id},
                ["content-length-range", 1, max_bytes],
            ],
            ExpiresIn=_UPLOAD_URL_TTL_SECONDS,
        )
        return {
            "url": post["url"],
            "method": "POST",
            "fields": post["fields"],
            "expires_in": _UPLOAD_URL_TTL_SECONDS,
        }

    def head(self, key: str) -> dict[str, Any]:
        """Return object metadata without downloading policy content."""
        return self._s3.head_object(Bucket=self._bucket, Key=key)

    def get_bytes(self, key: str, *, max_bytes: int | None = MAX_DOCX_BYTES) -> bytes:
        """Download an object, refusing anything over ``max_bytes``.

        The size is checked with HeadObject first so an oversized object is
        never pulled into the Lambda's memory at all. The upload POST policy's
        content-length-range already bounds new uploads; this guards objects
        that predate it (or arrive by any other path).
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
            params["ResponseContentDisposition"] = content_disposition(download_name)
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

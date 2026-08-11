"""DocumentStore presigned URLs must be SigV4.

Buckets created after 2020-06 reject SigV2 presigned requests with 403, and
the default boto3 client in legacy-listed regions (us-gov-west-1 included)
emits SigV2-form presigned URLs unless the client pins s3v4. The stig-parser
sibling hit exactly this on its first real upload after the public flip.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rmf_migrator.common.limits import MAX_DOC_BYTES, ConversionMissing
from rmf_migrator.common.models import Document
from rmf_migrator.common.storage import (
    DOC_CONTENT_TYPE,
    DOCX_CONTENT_TYPE,
    DocumentStore,
    build_converted_document_key,
    build_document_key,
)


def test_presigned_urls_are_sigv4(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-gov-west-1")
    store = DocumentStore("test-bucket", "test-kms-key")

    get = store.presigned_get_url("projects/p/documents/d.docx")["url"]
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in get
    assert "AWSAccessKeyId=" not in get
    # Upload target: the POST policy's x-amz-algorithm field is the SigV4 marker.
    post = store.presigned_post("projects/p/documents/d.docx")
    assert post["fields"]["x-amz-algorithm"] == "AWS4-HMAC-SHA256"


def _disposition_from(url: str) -> str:
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(url).query)["response-content-disposition"][0]


def test_download_name_cannot_break_out_of_the_disposition_header(monkeypatch):
    """Quotes, backslashes, and control characters in a stored filename must
    never reach the Content-Disposition header verbatim — a crafted filename
    could otherwise smuggle extra parameters or header lines into the S3
    response."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-gov-west-1")
    store = DocumentStore("test-bucket", "test-kms-key")

    url = store.presigned_get_url("k", download_name='a"; foo="bar\r\nX-Evil: 1\.docx')["url"]
    disposition = _disposition_from(url)
    assert '"' not in disposition.removeprefix('attachment; filename="').removesuffix('"')
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert "\\" not in disposition
    assert disposition.startswith('attachment; filename="')


def test_non_ascii_download_name_gets_rfc5987_fallback(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-gov-west-1")
    store = DocumentStore("test-bucket", "test-kms-key")

    url = store.presigned_get_url("k", download_name="Richtlinie-für-IT.docx")["url"]
    disposition = _disposition_from(url)
    # ASCII-only quoted fallback plus an RFC 5987 filename* carrying the real name.
    assert disposition.startswith('attachment; filename="')
    assert "filename*=UTF-8''" in disposition
    assert "ü" not in disposition.split("filename*")[0]


def test_presigned_post_pins_size_ceiling_and_encryption(monkeypatch):
    """Presigned PUT cannot carry an S3-side size limit; only a POST policy's
    content-length-range can stop an oversized object from ever landing.
    The policy must also keep pinning CMK encryption, like the PUT did."""
    import base64
    import json as jsonlib

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-gov-west-1")
    from rmf_migrator.common.limits import MAX_DOCX_BYTES

    store = DocumentStore("test-bucket", "test-kms-key")
    target = store.presigned_post("projects/p/documents/d.docx")

    assert target["method"] == "POST"
    assert target["fields"]["Content-Type"].startswith("application/vnd.openxmlformats")
    assert target["fields"]["x-amz-server-side-encryption"] == "aws:kms"
    assert target["fields"]["x-amz-server-side-encryption-aws-kms-key-id"] == "test-kms-key"
    # SigV4, like every other presigned request in this app.
    assert target["fields"]["x-amz-algorithm"] == "AWS4-HMAC-SHA256"

    policy = jsonlib.loads(base64.b64decode(target["fields"]["policy"]))
    assert ["content-length-range", 1, MAX_DOCX_BYTES] in [
        list(c) if isinstance(c, list) else c for c in policy["conditions"]
    ]


def test_presigned_post_pins_doc_content_type_and_tighter_cap(monkeypatch):
    """A legacy upload target must pin its own content type and its own,
    tighter ceiling, and the format asked for must decide both: naming the
    format alone means 25 MB of OLE2 can never ride in on a policy written
    for compressed XML."""
    import base64
    import json as jsonlib

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-gov-west-1")

    store = DocumentStore("test-bucket", "test-kms-key")
    target = store.presigned_post("projects/p/documents/d.doc", source_format="doc")

    assert target["fields"]["Content-Type"] == DOC_CONTENT_TYPE
    policy = jsonlib.loads(base64.b64decode(target["fields"]["policy"]))
    conditions = [list(c) if isinstance(c, list) else c for c in policy["conditions"]]
    assert ["content-length-range", 1, MAX_DOC_BYTES] in conditions
    assert {"Content-Type": DOC_CONTENT_TYPE} in conditions


def test_presigned_post_still_defaults_to_docx(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-gov-west-1")

    store = DocumentStore("test-bucket", "test-kms-key")
    target = store.presigned_post("projects/p/documents/d.docx")
    assert target["fields"]["Content-Type"] == DOCX_CONTENT_TYPE


def test_presigned_post_refuses_a_policy_that_disagrees_with_the_key(monkeypatch):
    """The fail-open direction is a .doc key issued the looser .docx policy:
    a caller that forgets the format would mint a 25 MB target for OLE2 bytes,
    and the converter's MAX_DOC_BYTES ceiling can no longer be enforced once
    the bytes are in the bucket. The key suffix and the policy must agree, and
    disagreement is refused rather than resolved in the caller's favour."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-gov-west-1")

    store = DocumentStore("test-bucket", "test-kms-key")

    with pytest.raises(ValueError):
        store.presigned_post("projects/p/documents/d.doc")
    with pytest.raises(ValueError):
        store.presigned_post("projects/p/documents/d.docx", source_format="doc")
    with pytest.raises(ValueError):
        store.presigned_post("projects/p/documents/d.xls", source_format="xls")


def test_converted_key_is_distinct_from_the_original():
    original = build_document_key("proj_1", "doc_1", "policy.doc")
    converted = build_converted_document_key("proj_1", "doc_1")
    assert converted != original
    assert converted.endswith(".converted.docx")


def test_document_key_preserves_the_uploaded_extension():
    # A legacy upload must not be stored under a .docx key: the key is what the
    # worker fetches, and a .doc parked at .docx reads as an already-converted
    # document. The .docx case is the regression guard — existing objects keep
    # the exact key they had before the suffix was derived from the filename.
    assert build_document_key("p", "d", "policy.doc") == "projects/p/documents/d.doc"
    assert build_document_key("p", "d", "policy.docx") == "projects/p/documents/d.docx"


def test_document_defaults_to_docx_with_no_conversion():
    doc = Document(project_id="proj_1", filename="policy.docx", s3_key="k")
    assert doc.source_format == "docx"
    assert doc.converted_s3_key is None


def test_document_records_conversion_provenance():
    doc = Document(
        project_id="proj_1",
        filename="policy.doc",
        s3_key="k",
        source_format="doc",
        converted_s3_key="projects/proj_1/documents/doc_1.converted.docx",
    )
    assert doc.source_format == "doc"
    assert doc.converted_s3_key is not None


def test_parse_key_refuses_an_unconverted_legacy_upload():
    """A .doc with no converted object is a conversion that did not happen —
    the Lambda timed out, the backend was flipped back to `reject`, or the
    convert stage raised. Falling back to s3_key there would hand OLE2 bytes
    to the .docx parser, which is exactly what this seam exists to prevent,
    and FAILED documents are re-admitted for retry on demand."""
    doc = Document(
        project_id="proj_1",
        filename="policy.doc",
        s3_key="projects/proj_1/documents/doc_1.doc",
        source_format="doc",
    )
    with pytest.raises(ConversionMissing):
        doc.parse_key()


def test_parse_key_refuses_an_empty_converted_key():
    # An empty converted key is absence, not a key. If the guard tested `is
    # None` while the return tested truthiness, "" would slip past the guard
    # and fall back to the raw OLE2 key.
    doc = Document(
        project_id="proj_1",
        filename="policy.doc",
        s3_key="projects/proj_1/documents/doc_1.doc",
        source_format="doc",
        converted_s3_key="",
    )
    with pytest.raises(ConversionMissing):
        doc.parse_key()


def test_document_rejects_an_unknown_source_format():
    """source_format is a two-value provenance flag on a CUI record, not free
    text; anything else means a caller invented a format the pipeline cannot
    read."""
    with pytest.raises(ValidationError):
        Document(project_id="proj_1", filename="policy.pdf", s3_key="k", source_format="pdf")

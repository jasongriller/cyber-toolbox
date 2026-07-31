"""DocumentStore presigned URLs must be SigV4.

Buckets created after 2020-06 reject SigV2 presigned requests with 403, and
the default boto3 client in legacy-listed regions (us-gov-west-1 included)
emits SigV2-form presigned URLs unless the client pins s3v4. The stig-parser
sibling hit exactly this on its first real upload after the public flip.
"""

from __future__ import annotations

from rmf_migrator.common.storage import DocumentStore


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

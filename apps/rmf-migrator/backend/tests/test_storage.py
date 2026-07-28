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

    put = store.presigned_put_url("projects/p/documents/d.docx")["url"]
    get = store.presigned_get_url("projects/p/documents/d.docx")["url"]
    for url in (put, get):
        assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
        assert "AWSAccessKeyId=" not in url

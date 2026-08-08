# Legacy .doc Import Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept legacy binary `.doc` uploads by routing them through a swappable conversion seam, so the parser and exporter continue to see only `.docx`.

**Architecture:** A narrow `DocConverter` port (`convert(bytes) -> bytes`) sits between ingest and parsing. Two backends ship: `RejectingConverter` (default — preserves today's behavior with a clearer message) and `LibreOfficeLambdaConverter` (invokes a container-image Lambda, deployed only behind a Terraform flag pending the ISSO decision). The worker sniffs magic bytes, converts OLE2 input to a separate S3 key, and parses the converted bytes. The original `.doc` is never modified — it stays as audit provenance.

**Tech Stack:** Python 3.12, pydantic v2, boto3, python-docx, pytest + moto; TypeScript/React + vitest; Terraform; Docker (container-image Lambda).

**Spec:** [2026-08-07-doc-conversion-design.md](../specs/2026-08-07-doc-conversion-design.md)

---

## Design refinement requiring sign-off

The spec (§2.4) said the frontend sniff should become "allowed, tagged for conversion **when the API reports conversion enabled**." Reading `client.ts` showed the sniff runs *before* any API call, so there is no API answer available at that moment. Adding a capabilities endpoint just to answer it would introduce build-time/runtime config drift.

**Refinement:** the frontend decides from **bytes + filename extension**, and the backend stays the single source of truth for whether conversion is enabled:

| Bytes | Filename | Frontend behavior |
|---|---|---|
| zip (`PK\x03\x04`) | anything | allow (as today) |
| OLE2 (`D0CF11E0`) | ends `.doc` | **allow** — backend returns 400 with guidance if conversion is off |
| OLE2 (`D0CF11E0`) | ends `.docx` | **reject** — genuine mismatch; keep today's "renamed legacy .doc" message |
| HTML / PDF / empty | anything | reject (as today) |

This preserves the existing rejection for a renamed file (a real user error worth naming) while letting honest `.doc` files through, and needs no new endpoint.

## File structure

**Create:**
- `backend/src/rmf_migrator/doc_convert/__init__.py` — public exports
- `backend/src/rmf_migrator/doc_convert/base.py` — `DocConverter` protocol + exceptions
- `backend/src/rmf_migrator/doc_convert/rejecting.py` — default backend
- `backend/src/rmf_migrator/doc_convert/lambda_backend.py` — LibreOffice Lambda backend
- `backend/src/rmf_migrator/converter_lambda/handler.py` — runs inside the container image
- `backend/converter.Dockerfile` — container image definition
- `backend/tests/test_doc_convert.py`, `test_convert_ingest.py`, `test_converter_lambda.py`
- `terraform/modules/rmf-migrator/converter.tf`

**Modify:**
- `backend/src/rmf_migrator/common/limits.py` — format sniff + `.doc` ceiling
- `backend/src/rmf_migrator/common/storage.py` — `.doc` POST policy, converted key
- `backend/src/rmf_migrator/common/models.py` — `source_format`, `converted_s3_key`
- `backend/src/rmf_migrator/common/config.py` — backend selection
- `backend/src/rmf_migrator/handlers/deps.py` — converter assembly
- `backend/src/rmf_migrator/handlers/request_upload.py` — accept `.doc`
- `backend/src/rmf_migrator/handlers/parse_document.py` — convert-then-parse
- `frontend/src/api/client.ts`, `types.ts`, `components/ProjectBrowser.tsx`

---

### Task 1: Format sniff and `.doc` size ceiling

**Files:**
- Modify: `backend/src/rmf_migrator/common/limits.py`
- Test: `backend/tests/test_limits.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_limits.py`:

```python
from rmf_migrator.common.limits import MAX_DOC_BYTES, sniff_document_format

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def test_sniff_document_format_identifies_docx():
    assert sniff_document_format(_real_docx()) == "docx"


def test_sniff_document_format_identifies_legacy_doc():
    assert sniff_document_format(OLE2_MAGIC + b"rest of an OLE2 file") == "doc"


def test_sniff_document_format_rejects_impostors():
    assert sniff_document_format(b"%PDF-1.7 ...") is None
    assert sniff_document_format(b"<!doctype html><html></html>") is None
    assert sniff_document_format(b"") is None


def test_doc_ceiling_is_tighter_than_docx():
    assert MAX_DOC_BYTES < MAX_DOCX_BYTES


def test_sniff_docx_problem_still_names_a_renamed_legacy_doc():
    problem = sniff_docx_problem(OLE2_MAGIC + b"rest")
    assert problem is not None
    assert "legacy Word .doc" in problem
```

Add `sniff_docx_problem` to the existing `from rmf_migrator.common.limits import (...)` block at the top of the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_limits.py -v -k "sniff_document_format or doc_ceiling"`
Expected: FAIL with `ImportError: cannot import name 'MAX_DOC_BYTES'`

- [ ] **Step 3: Write minimal implementation**

In `limits.py`, add below `MAX_DOCX_BYTES`:

```python
# Largest legacy binary .doc we accept for conversion. Tighter than the .docx
# ceiling: OLE2 is uncompressed, so 15 MB of .doc is far more content than
# 15 MB of zipped XML, and the converter is the newest attack surface here.
MAX_DOC_BYTES = 15 * 1024 * 1024  # 15 MB
```

Add above `sniff_docx_problem`:

```python
_ZIP_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def sniff_document_format(data: bytes) -> str | None:
    """Classify Word document bytes: "docx", "doc", or None if neither.

    Extension is not evidence — a renamed file lies about its format, so the
    routing decision is made from magic bytes alone.
    """
    if data[:4] == _ZIP_MAGIC:
        return "docx"
    if data[:8] == _OLE2_MAGIC:
        return "doc"
    return None
```

Then rewrite the first two branches of `sniff_docx_problem` to reuse it:

```python
    if not data:
        return "file is empty"
    fmt = sniff_document_format(data)
    if fmt == "docx":
        return None
    if fmt == "doc":
        return (
            "this is a legacy Word .doc renamed to .docx — open it in Word "
            "and use Save As to create a real .docx"
        )
```

Leave the HTML/PDF/fallback branches below unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_limits.py -v`
Expected: PASS — all pre-existing tests included (the `sniff_docx_problem` refactor must not change any existing behavior)

- [ ] **Step 5: Commit**

```bash
git add apps/rmf-migrator/backend/src/rmf_migrator/common/limits.py apps/rmf-migrator/backend/tests/test_limits.py
git commit -m "feat(limits): classify doc/docx by magic bytes, add .doc ceiling"
```

---

### Task 2: The conversion port and rejecting backend

**Files:**
- Create: `backend/src/rmf_migrator/doc_convert/__init__.py`, `base.py`, `rejecting.py`
- Test: `backend/tests/test_doc_convert.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_doc_convert.py`:

```python
"""Tests for the .doc conversion seam."""

from __future__ import annotations

import pytest

from rmf_migrator.doc_convert import (
    ConversionFailed,
    ConversionUnavailable,
    RejectingConverter,
)


def test_rejecting_converter_raises_unavailable():
    with pytest.raises(ConversionUnavailable):
        RejectingConverter().convert(b"\xd0\xcf\x11\xe0anything")


def test_rejecting_converter_message_tells_the_user_what_to_do():
    with pytest.raises(ConversionUnavailable) as excinfo:
        RejectingConverter().convert(b"\xd0\xcf\x11\xe0anything")
    message = str(excinfo.value)
    assert "Save As" in message
    assert "not enabled" in message


def test_conversion_errors_are_distinct_types():
    assert not issubclass(ConversionFailed, ConversionUnavailable)
    assert not issubclass(ConversionUnavailable, ConversionFailed)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_doc_convert.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rmf_migrator.doc_convert'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/src/rmf_migrator/doc_convert/base.py`:

```python
"""The .doc -> .docx conversion port.

The pipeline depends only on this protocol, never on a concrete converter, so
the deployment decision (LibreOffice in the accreditation boundary, an
out-of-boundary service, or no conversion at all) is a configuration choice
rather than a code change. See docs/superpowers/specs/2026-08-07-doc-conversion-design.md.
"""

from __future__ import annotations

from typing import Protocol


class ConversionUnavailable(Exception):
    """No conversion backend is enabled in this environment.

    Distinct from ConversionFailed: the input may be perfectly valid. This is a
    deployment state, so it is reported to the user at upload time rather than
    as a failed parse job.
    """


class ConversionFailed(Exception):
    """The backend was reached but could not produce a usable .docx.

    Covers bad input, timeout, and output that breaches a size ceiling.
    """


class DocConverter(Protocol):
    def convert(self, data: bytes) -> bytes:
        """Convert legacy binary .doc bytes to .docx bytes.

        Raises ConversionUnavailable or ConversionFailed.
        """
        ...
```

Create `backend/src/rmf_migrator/doc_convert/rejecting.py`:

```python
"""Default backend: no conversion available.

Deployed behavior matches the pre-conversion tool exactly, except the message
distinguishes "this environment has conversion switched off" from "your file is
broken" — the user's next action differs.
"""

from __future__ import annotations

from .base import ConversionUnavailable

_MESSAGE = (
    "legacy .doc conversion is not enabled in this environment — open the "
    "file in Word and use Save As to create a .docx, then upload that"
)


class RejectingConverter:
    def convert(self, data: bytes) -> bytes:
        raise ConversionUnavailable(_MESSAGE)
```

Create `backend/src/rmf_migrator/doc_convert/__init__.py`:

```python
"""Legacy .doc conversion seam."""

from .base import ConversionFailed, ConversionUnavailable, DocConverter
from .rejecting import RejectingConverter

__all__ = [
    "ConversionFailed",
    "ConversionUnavailable",
    "DocConverter",
    "RejectingConverter",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_doc_convert.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/rmf-migrator/backend/src/rmf_migrator/doc_convert apps/rmf-migrator/backend/tests/test_doc_convert.py
git commit -m "feat(doc_convert): add conversion port and rejecting backend"
```

---

### Task 3: LibreOffice Lambda backend

The Lambda invoke payload cap (6 MB synchronous) is smaller than `MAX_DOC_BYTES`, so bytes are staged through S3 rather than passed inline. Staging lives inside this backend so the port stays bytes-in/bytes-out and any future backend (e.g. an out-of-boundary HTTP service) can implement it without S3 at all.

**Files:**
- Create: `backend/src/rmf_migrator/doc_convert/lambda_backend.py`
- Modify: `backend/src/rmf_migrator/doc_convert/__init__.py`
- Test: `backend/tests/test_doc_convert.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_doc_convert.py`:

```python
import json

from rmf_migrator.doc_convert import LibreOfficeLambdaConverter


class _FakeStore:
    """Minimal DocumentStore stand-in recording staged writes and deletes."""

    def __init__(self, converted: bytes = b"PK\x03\x04converted"):
        self.written: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self._converted = converted

    def put_bytes(self, key: str, data: bytes, content_type: str = "") -> None:
        self.written[key] = data

    def get_bytes(self, key: str, *, max_bytes: int | None = None) -> bytes:
        return self._converted

    def delete_key(self, key: str) -> None:
        self.deleted.append(key)


class _FakeLambdaClient:
    def __init__(self, status: int = 200, error: str | None = None):
        self.status = status
        self.error = error
        self.calls: list[dict] = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        body = {"ok": self.error is None}
        if self.error:
            body["error"] = self.error
        response = {
            "StatusCode": self.status,
            "Payload": _Payload(json.dumps(body).encode()),
        }
        if self.error:
            response["FunctionError"] = "Unhandled"
        return response


class _Payload:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


def _converter(store, client) -> LibreOfficeLambdaConverter:
    return LibreOfficeLambdaConverter(
        function_name="rmf-doc-converter",
        bucket="docs-bucket",
        store=store,
        lambda_client=client,
    )


def test_lambda_backend_returns_converted_bytes():
    store, client = _FakeStore(), _FakeLambdaClient()
    assert _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc") == b"PK\x03\x04converted"


def test_lambda_backend_stages_input_and_cleans_up_both_scratch_keys():
    store, client = _FakeStore(), _FakeLambdaClient()
    _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc")

    assert len(store.written) == 1
    source_key = next(iter(store.written))
    assert source_key.startswith("convert-scratch/")
    assert source_key.endswith(".doc")
    # Both the staged input and the converter's output are scratch and must go.
    assert len(store.deleted) == 2
    assert source_key in store.deleted


def test_lambda_backend_passes_keys_in_the_invoke_payload():
    store, client = _FakeStore(), _FakeLambdaClient()
    _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc")

    payload = json.loads(client.calls[0]["Payload"])
    assert payload["bucket"] == "docs-bucket"
    assert payload["source_key"].endswith(".doc")
    assert payload["target_key"].endswith(".docx")


def test_lambda_backend_raises_failed_when_the_function_errors():
    store, client = _FakeStore(), _FakeLambdaClient(error="soffice exited 1")
    with pytest.raises(ConversionFailed):
        _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc")


def test_lambda_backend_cleans_up_scratch_even_on_failure():
    store, client = _FakeStore(), _FakeLambdaClient(error="soffice exited 1")
    with pytest.raises(ConversionFailed):
        _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc")
    assert len(store.deleted) == 2


def test_lambda_backend_rejects_oversized_input_without_invoking():
    store, client = _FakeStore(), _FakeLambdaClient()
    with pytest.raises(ConversionFailed):
        _converter(store, client).convert(b"\xd0\xcf\x11\xe0" + b"x" * MAX_DOC_BYTES)
    assert client.calls == []


def test_lambda_backend_rejects_output_that_is_not_a_docx():
    store, client = _FakeStore(converted=b"%PDF-1.7 not a docx"), _FakeLambdaClient()
    with pytest.raises(ConversionFailed):
        _converter(store, client).convert(b"\xd0\xcf\x11\xe0doc")
```

Add `from rmf_migrator.common.limits import MAX_DOC_BYTES` to the imports at the top of the test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_doc_convert.py -v`
Expected: FAIL with `ImportError: cannot import name 'LibreOfficeLambdaConverter'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/src/rmf_migrator/doc_convert/lambda_backend.py`:

```python
"""LibreOffice conversion backend, invoked as a separate container-image Lambda.

Isolation is the point: LibreOffice parses a macro-bearing binary format with a
long CVE history, so it runs in its own function with its own minimal role and
no network egress, never inside the API or parse workers.

Bytes travel via S3 scratch keys because a 15 MB .doc exceeds Lambda's 6 MB
synchronous invoke payload. Staging is an implementation detail of this backend
so the DocConverter port stays bytes-in/bytes-out.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from rmf_migrator.common.limits import MAX_DOC_BYTES, MAX_DOCX_BYTES, sniff_document_format

from .base import ConversionFailed

_SCRATCH_PREFIX = "convert-scratch"


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
            import boto3

            from rmf_migrator.common.aws_clients import FAST_CONFIG

            lambda_client = boto3.client("lambda", config=FAST_CONFIG)
        self._lambda = lambda_client

    def convert(self, data: bytes) -> bytes:
        if len(data) > MAX_DOC_BYTES:
            raise ConversionFailed(f"legacy .doc exceeds {MAX_DOC_BYTES} bytes")

        token = uuid.uuid4().hex
        source_key = f"{_SCRATCH_PREFIX}/{token}.doc"
        target_key = f"{_SCRATCH_PREFIX}/{token}.docx"
        try:
            self._store.put_bytes(source_key, data, "application/msword")
            self._invoke(source_key, target_key)
            converted = self._store.get_bytes(target_key, max_bytes=MAX_DOCX_BYTES)
        finally:
            # Scratch must not survive a failure: it holds document content.
            for key in (source_key, target_key):
                try:
                    self._store.delete_key(key)
                except Exception:  # noqa: BLE001 — cleanup is best-effort
                    pass

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
```

Add to `doc_convert/__init__.py`:

```python
from .lambda_backend import LibreOfficeLambdaConverter
```

and add `"LibreOfficeLambdaConverter",` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_doc_convert.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Add `delete_key` to the real store**

`_FakeStore` implements `delete_key`; `DocumentStore` does not yet. Add to `backend/src/rmf_migrator/common/storage.py`:

```python
    def delete_key(self, key: str) -> None:
        """Delete a single object (conversion scratch; not versioned cleanup)."""
        self._s3.delete_object(Bucket=self._bucket, Key=key)
```

- [ ] **Step 6: Commit**

```bash
git add apps/rmf-migrator/backend/src/rmf_migrator/doc_convert apps/rmf-migrator/backend/src/rmf_migrator/common/storage.py apps/rmf-migrator/backend/tests/test_doc_convert.py
git commit -m "feat(doc_convert): add LibreOffice Lambda backend with S3 staging"
```

---

### Task 4: Config and Deps wiring

**Files:**
- Modify: `backend/src/rmf_migrator/common/config.py`, `backend/src/rmf_migrator/handlers/deps.py`
- Test: `backend/tests/test_doc_convert.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_doc_convert.py`:

```python
from rmf_migrator.common.config import Config
from rmf_migrator.doc_convert import build_converter


def _config(**overrides) -> Config:
    base = dict(
        documents_bucket="docs-bucket",
        table_name="t",
        kms_key_id="k",
        parse_queue_url="q",
        bedrock_model_id="m",
        bedrock_region="us-gov-west-1",
        identity_header=None,
        bedrock_guardrail_id=None,
        bedrock_guardrail_version=None,
        doc_conversion_backend="reject",
        doc_converter_function_name=None,
    )
    base.update(overrides)
    return Config(**base)


def test_build_converter_defaults_to_rejecting():
    assert isinstance(build_converter(_config(), store=_FakeStore()), RejectingConverter)


def test_build_converter_returns_lambda_backend_when_configured():
    converter = build_converter(
        _config(doc_conversion_backend="lambda", doc_converter_function_name="fn"),
        store=_FakeStore(),
    )
    assert isinstance(converter, LibreOfficeLambdaConverter)


def test_build_converter_rejects_lambda_backend_without_a_function_name():
    with pytest.raises(ValueError):
        build_converter(_config(doc_conversion_backend="lambda"), store=_FakeStore())


def test_conversion_enabled_reflects_the_backend():
    assert _config().conversion_enabled is False
    assert _config(
        doc_conversion_backend="lambda", doc_converter_function_name="fn"
    ).conversion_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_doc_convert.py -v -k build_converter`
Expected: FAIL with `ImportError: cannot import name 'build_converter'`

- [ ] **Step 3: Write minimal implementation**

In `config.py`, add two fields to `Config` after `bedrock_guardrail_version`:

```python
    # Legacy .doc conversion. "reject" (default) keeps .doc uploads out; "lambda"
    # routes them through the LibreOffice converter function. Gated on the ISSO
    # decision recorded in the design spec.
    doc_conversion_backend: str = "reject"
    doc_converter_function_name: str | None = None

    @property
    def conversion_enabled(self) -> bool:
        return self.doc_conversion_backend != "reject"
```

and to `from_env()`:

```python
            doc_conversion_backend=os.environ.get("DOC_CONVERSION_BACKEND", "reject"),
            doc_converter_function_name=os.environ.get("DOC_CONVERTER_FUNCTION_NAME") or None,
```

Create `backend/src/rmf_migrator/doc_convert/factory.py`:

```python
"""Backend selection. One config value decides; nothing else in the pipeline
knows which converter is in play."""

from __future__ import annotations

from typing import Any

from .base import DocConverter
from .lambda_backend import LibreOfficeLambdaConverter
from .rejecting import RejectingConverter


def build_converter(config: Any, *, store: Any) -> DocConverter:
    backend = config.doc_conversion_backend
    if backend == "reject":
        return RejectingConverter()
    if backend == "lambda":
        if not config.doc_converter_function_name:
            raise ValueError(
                "DOC_CONVERSION_BACKEND=lambda requires DOC_CONVERTER_FUNCTION_NAME"
            )
        return LibreOfficeLambdaConverter(
            function_name=config.doc_converter_function_name,
            bucket=config.documents_bucket,
            store=store,
        )
    raise ValueError(f"unknown DOC_CONVERSION_BACKEND: {backend!r}")
```

Export it from `doc_convert/__init__.py` (`from .factory import build_converter`, plus `__all__`).

In `deps.py`, add the field and wire it:

```python
    converter: Any = None
```

and inside `build()`, after `store` is constructed:

```python
            store = DocumentStore(config.documents_bucket, config.kms_key_id)
            _built = Deps(
                config=config,
                repo=Repository(config.table_name),
                store=store,
                sqs=boto3.client("sqs", config=FAST_CONFIG),
                bedrock=BedrockClient.from_config(config),
                converter=build_converter(config, store=store),
            )
```

Add `from rmf_migrator.doc_convert import build_converter` to the imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_doc_convert.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/rmf-migrator/backend/src/rmf_migrator apps/rmf-migrator/backend/tests/test_doc_convert.py
git commit -m "feat(config): select .doc conversion backend from environment"
```

---

### Task 5: Document provenance fields and converted-document key

**Files:**
- Modify: `backend/src/rmf_migrator/common/models.py`, `backend/src/rmf_migrator/common/storage.py`
- Test: `backend/tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_storage.py`:

```python
from rmf_migrator.common.models import Document
from rmf_migrator.common.storage import build_converted_document_key


def test_converted_key_is_distinct_from_the_original():
    original = build_document_key("proj_1", "doc_1", "policy.doc")
    converted = build_converted_document_key("proj_1", "doc_1")
    assert converted != original
    assert converted.endswith(".converted.docx")


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
```

Add `build_document_key` to the existing storage import block if not already present.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_storage.py -v -k "converted or provenance or defaults_to_docx"`
Expected: FAIL with `ImportError: cannot import name 'build_converted_document_key'`

- [ ] **Step 3: Write minimal implementation**

In `storage.py`, add after `build_document_key`:

```python
DOC_CONTENT_TYPE = "application/msword"


def build_converted_document_key(project_id: str, document_id: str) -> str:
    """S3 key for the .docx produced from an uploaded legacy .doc.

    A separate key, never an overwrite: the original .doc is audit provenance
    for the A&A package and must remain byte-identical to what was uploaded.
    """
    return f"projects/{project_id}/documents/{document_id}.converted.docx"
```

Change `build_document_key` to preserve the real extension so a `.doc` is not stored under a `.docx` key:

```python
def build_document_key(project_id: str, document_id: str, filename: str) -> str:
    """Deterministic S3 key. Filename is stored in metadata, not the key, to
    avoid leaking potentially sensitive names into access logs / URLs."""
    suffix = ".doc" if filename.lower().endswith(".doc") else ".docx"
    return f"projects/{project_id}/documents/{document_id}{suffix}"
```

In `models.py`, add to `Document` after `export_key`:

```python
    # Provenance for a converted upload. source_format is what the user sent;
    # converted_s3_key points at the .docx everything downstream actually reads.
    source_format: str = "docx"
    converted_s3_key: str | None = None

    def parse_key(self) -> str:
        """The S3 key holding parseable .docx bytes for this document."""
        return self.converted_s3_key or self.s3_key
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_storage.py tests/test_handlers.py -v`
Expected: PASS — including pre-existing key-format assertions (update any that hardcode `.docx` for a `.docx` filename only if they now assert the wrong thing; a `.docx` upload must still produce the identical key it did before)

- [ ] **Step 5: Commit**

```bash
git add apps/rmf-migrator/backend/src/rmf_migrator/common apps/rmf-migrator/backend/tests/test_storage.py
git commit -m "feat(models): record .doc conversion provenance on Document"
```

---

### Task 6: `.doc` presigned POST policy

**Files:**
- Modify: `backend/src/rmf_migrator/common/storage.py`
- Test: `backend/tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_storage.py`:

```python
from rmf_migrator.common.limits import MAX_DOC_BYTES
from rmf_migrator.common.storage import DOC_CONTENT_TYPE


def test_presigned_post_pins_doc_content_type_and_tighter_cap(document_store):
    target = document_store.presigned_post(
        "projects/p/documents/d.doc",
        content_type=DOC_CONTENT_TYPE,
        max_bytes=MAX_DOC_BYTES,
    )
    assert target["fields"]["Content-Type"] == DOC_CONTENT_TYPE


def test_presigned_post_still_defaults_to_docx(document_store):
    target = document_store.presigned_post("projects/p/documents/d.docx")
    assert target["fields"]["Content-Type"] == DOCX_CONTENT_TYPE
```

Use whatever fixture the existing file uses to build a moto-backed `DocumentStore`; if it constructs one inline, follow that pattern instead of a `document_store` fixture.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_storage.py -v -k presigned_post`
Expected: FAIL with `TypeError: presigned_post() got an unexpected keyword argument 'content_type'`

- [ ] **Step 3: Write minimal implementation**

Change the signature and body of `presigned_post` in `storage.py`:

```python
    def presigned_post(
        self,
        key: str,
        *,
        content_type: str = DOCX_CONTENT_TYPE,
        max_bytes: int = MAX_DOCX_BYTES,
    ) -> dict[str, Any]:
        """Return a presigned POST target with an S3-side size ceiling.

        Presigned PUT cannot express a size limit, so an oversized object
        could land in the bucket before the app-side check rejected it (a
        storage-cost/DoS vector). A POST policy's content-length-range makes
        S3 itself refuse anything over ``max_bytes``, and the pinned fields
        keep enforcing CMK encryption exactly as the PUT headers did.

        Legacy .doc uploads pass a distinct content type and a tighter cap;
        pinning the type means a .doc cannot be uploaded through a .docx
        target or vice versa.
        """
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
```

Add `MAX_DOC_BYTES` to the `from rmf_migrator.common.limits import ...` line.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_storage.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/rmf-migrator/backend/src/rmf_migrator/common/storage.py apps/rmf-migrator/backend/tests/test_storage.py
git commit -m "feat(storage): parameterize presigned POST content type and size cap"
```

---

### Task 7: Accept `.doc` at upload registration

**Files:**
- Modify: `backend/src/rmf_migrator/handlers/request_upload.py`
- Test: `backend/tests/test_convert_ingest.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_convert_ingest.py`:

```python
"""Ingest-path tests for legacy .doc uploads."""

from __future__ import annotations

import json

from rmf_migrator.common.storage import DOC_CONTENT_TYPE
from rmf_migrator.handlers.request_upload import _request_upload


def _event(project_id: str, filename: str) -> dict:
    return {
        "pathParameters": {"project_id": project_id},
        "body": json.dumps({"filename": filename}),
    }


def test_doc_upload_rejected_when_conversion_disabled(deps_with_project):
    deps, project_id = deps_with_project
    deps.config = deps.config.__class__(
        **{**deps.config.__dict__, "doc_conversion_backend": "reject"}
    )
    response = _request_upload(_event(project_id, "policy.doc"), deps)
    assert response["statusCode"] == 400
    assert "not enabled" in json.loads(response["body"])["error"]


def test_doc_upload_accepted_when_conversion_enabled(deps_with_conversion):
    deps, project_id = deps_with_conversion
    response = _request_upload(_event(project_id, "policy.doc"), deps)
    assert response["statusCode"] == 201

    body = json.loads(response["body"])
    assert body["document"]["source_format"] == "doc"
    assert body["document"]["s3_key"].endswith(".doc")
    assert body["upload"]["fields"]["Content-Type"] == DOC_CONTENT_TYPE


def test_docx_upload_is_unaffected_by_conversion_config(deps_with_conversion):
    deps, project_id = deps_with_conversion
    response = _request_upload(_event(project_id, "policy.docx"), deps)
    assert response["statusCode"] == 201
    assert json.loads(response["body"])["document"]["source_format"] == "docx"


def test_other_extensions_still_rejected(deps_with_conversion):
    deps, project_id = deps_with_conversion
    response = _request_upload(_event(project_id, "policy.pdf"), deps)
    assert response["statusCode"] == 400
```

Add `deps_with_project` and `deps_with_conversion` fixtures to `backend/tests/conftest.py`, following the existing moto/fake construction used by `test_handlers.py`. `deps_with_conversion` differs only in `doc_conversion_backend="lambda"` and `doc_converter_function_name="fn"`.

Note: `_request_upload` raises `HttpError` rather than returning a response. Wrap the calls the same way `handler` does, or assert with `pytest.raises(HttpError)` and check `.status`/`.message` — match whichever style `test_handlers.py` already uses and keep this file consistent with it.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_convert_ingest.py -v`
Expected: FAIL — `.doc` filenames rejected by the extension gate

- [ ] **Step 3: Write minimal implementation**

In `request_upload.py`, replace the extension check:

```python
    lowered = filename.lower()
    if lowered.endswith(".doc"):
        if not deps.config.conversion_enabled:
            raise HttpError(
                400,
                "legacy .doc conversion is not enabled in this environment — open "
                "the file in Word and use Save As to create a .docx, then upload that",
            )
        source_format = "doc"
    elif lowered.endswith(".docx"):
        source_format = "docx"
    else:
        raise HttpError(400, "only .docx and .doc files are supported")
```

Then set the field on the Document and pick the matching upload policy:

```python
    document = Document(
        project_id=project_id,
        filename=filename,
        s3_key="",  # set below once we have the id
        uploaded_by=identity,
        source_format=source_format,
    )
    document.s3_key = build_document_key(project_id, document.document_id, filename)
    deps.repo.put_document(document)
    deps.repo.increment_document_count(project_id)

    if source_format == "doc":
        upload = deps.store.presigned_post(
            document.s3_key, content_type=DOC_CONTENT_TYPE, max_bytes=MAX_DOC_BYTES
        )
    else:
        upload = deps.store.presigned_post(document.s3_key)
```

Add imports:

```python
from rmf_migrator.common.limits import MAX_DOC_BYTES
from rmf_migrator.common.storage import DOC_CONTENT_TYPE, build_document_key
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_convert_ingest.py tests/test_handlers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/rmf-migrator/backend/src/rmf_migrator/handlers/request_upload.py apps/rmf-migrator/backend/tests/test_convert_ingest.py apps/rmf-migrator/backend/tests/conftest.py
git commit -m "feat(upload): accept .doc registration when conversion is enabled"
```

---

### Task 8: Convert-then-parse in the worker

**Files:**
- Modify: `backend/src/rmf_migrator/handlers/parse_document.py`
- Test: `backend/tests/test_convert_ingest.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_convert_ingest.py`:

```python
import pytest

from rmf_migrator.common.models import DocumentStatus, JobStatus
from rmf_migrator.doc_convert import ConversionFailed
from rmf_migrator.handlers.parse_document import run_parse_job

OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy doc bytes"


class _StubConverter:
    def __init__(self, output: bytes | None = None, error: Exception | None = None):
        self.output = output
        self.error = error
        self.calls: list[bytes] = []

    def convert(self, data: bytes) -> bytes:
        self.calls.append(data)
        if self.error:
            raise self.error
        return self.output


def test_doc_document_is_converted_then_parsed(doc_job):
    deps, project_id, document_id, job_id = doc_job
    deps.converter = _StubConverter(output=_styled_docx_bytes())

    assert run_parse_job(project_id, document_id, job_id, deps) is True

    document = deps.repo.get_document(project_id, document_id)
    assert document.status == DocumentStatus.PARSED
    assert document.section_count > 0
    assert deps.converter.calls == [OLE2]


def test_converted_bytes_are_stored_under_a_separate_key(doc_job):
    deps, project_id, document_id, job_id = doc_job
    deps.converter = _StubConverter(output=_styled_docx_bytes())
    run_parse_job(project_id, document_id, job_id, deps)

    document = deps.repo.get_document(project_id, document_id)
    assert document.converted_s3_key is not None
    assert document.converted_s3_key != document.s3_key
    assert document.parse_key() == document.converted_s3_key
    # Original .doc preserved byte-for-byte as audit provenance.
    assert deps.store.get_bytes(document.s3_key, max_bytes=None) == OLE2


def test_docx_document_never_touches_the_converter(docx_job):
    deps, project_id, document_id, job_id = docx_job
    deps.converter = _StubConverter(error=AssertionError("must not be called"))

    assert run_parse_job(project_id, document_id, job_id, deps) is True
    assert deps.converter.calls == []


def test_conversion_failure_marks_the_document_failed_at_the_convert_stage(doc_job):
    deps, project_id, document_id, job_id = doc_job
    deps.converter = _StubConverter(error=ConversionFailed("soffice died"))

    with pytest.raises(ConversionFailed):
        run_parse_job(project_id, document_id, job_id, deps)

    document = deps.repo.get_document(project_id, document_id)
    assert document.status == DocumentStatus.FAILED
    assert document.failure_stage == "convert"
    assert document.parse_error == "ConversionFailed"
    # Error *type* only — no converter message leaks into the record.
    assert "soffice" not in (document.parse_error or "")

    job = deps.repo.get_job(project_id, job_id)
    assert job.status == JobStatus.FAILED
    assert job.error_type == "ConversionFailed"
```

Add a `_styled_docx_bytes()` helper (a python-docx document with a `Heading 1` and a body paragraph — copy the `_real_docx()` pattern from `test_limits.py`), plus `doc_job` and `docx_job` fixtures that seed a project, a `Document` (with `source_format` set and its bytes in the moto-backed store), and a PENDING `ParseJob`. Follow the fixture style already in `test_worker.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_convert_ingest.py -v -k "converted or converter"`
Expected: FAIL — the worker parses raw OLE2 bytes and raises `InvalidDocx`

- [ ] **Step 3: Write minimal implementation**

In `parse_document.py`, replace the two lines inside `try:` that fetch and parse:

```python
    try:
        data = deps.store.get_bytes(document.s3_key)
        parse_bytes, converted_key = _ensure_docx(data, document, deps)
        if converted_key is not None:
            document.converted_s3_key = converted_key
        sections = parse_docx_bytes(
            parse_bytes, document_id=document_id, project_id=project_id
        )
```

Add above `run_parse_job`:

```python
def _ensure_docx(
    data: bytes, document, deps: Deps  # noqa: ANN001
) -> tuple[bytes, str | None]:
    """Return parseable .docx bytes, converting a legacy .doc if needed.

    Routing is decided by magic bytes, not the filename: a mislabeled upload
    must be handled by what it actually is. The converted copy is written to
    its own key so the uploaded original stays byte-identical for audit.
    """
    if sniff_document_format(data) != "doc":
        return data, None

    converted = deps.converter.convert(data)
    key = build_converted_document_key(document.project_id, document.document_id)
    deps.store.put_bytes(key, converted)
    return converted, key
```

Add imports:

```python
from rmf_migrator.common.limits import sniff_document_format
from rmf_migrator.common.storage import build_converted_document_key
```

In the `except` block, set the failure stage from the error type:

```python
    except Exception as exc:  # noqa: BLE001 — worker must record failure, not crash silently
        document.status = DocumentStatus.FAILED
        document.parse_error = type(exc).__name__
        document.failure_stage = (
            "convert" if isinstance(exc, ConversionFailed | ConversionUnavailable) else "parse"
        )
```

Add `from rmf_migrator.doc_convert import ConversionFailed, ConversionUnavailable`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/ -v`
Expected: PASS — the whole backend suite, including `test_worker.py` and `test_export_worker.py`

- [ ] **Step 5: Point the exporter at the parsed bytes**

Export surgery must read the same bytes the parser read. In `backend/src/rmf_migrator/handlers/export.py`, find every `deps.store.get_bytes(document.s3_key)` and change it to `deps.store.get_bytes(document.parse_key())`. Run `grep -rn "document.s3_key" apps/rmf-migrator/backend/src` and change each read-for-parsing site; leave download/presign sites (which should serve the original) alone.

- [ ] **Step 6: Run the full suite again**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add apps/rmf-migrator/backend
git commit -m "feat(worker): convert legacy .doc before parsing, preserving the original"
```

---

### Task 9: Frontend sniff accepts honest `.doc`

**Files:**
- Modify: `frontend/src/api/client.ts`
- Test: `frontend/src/api/client.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/api/client.test.ts`:

```ts
import { sniffUploadProblem } from "./client";

const OLE2 = new Uint8Array([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]);
const ZIP = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x00]);

describe("sniffUploadProblem", () => {
  it("allows OLE2 bytes when the filename is honestly .doc", () => {
    expect(sniffUploadProblem(OLE2, "policy.doc")).toBeNull();
  });

  it("still rejects OLE2 bytes wearing a .docx name", () => {
    const problem = sniffUploadProblem(OLE2, "policy.docx");
    expect(problem).toContain("legacy Word .doc");
  });

  it("allows a real docx", () => {
    expect(sniffUploadProblem(ZIP, "policy.docx")).toBeNull();
  });

  it("rejects a PDF regardless of extension", () => {
    const pdf = new TextEncoder().encode("%PDF-1.7");
    expect(sniffUploadProblem(pdf, "policy.doc")).toContain("PDF");
    expect(sniffUploadProblem(pdf, "policy.docx")).toContain("PDF");
  });

  it("rejects an empty file", () => {
    expect(sniffUploadProblem(new Uint8Array(), "policy.doc")).toBe("file is empty");
  });
});

it("uploadDocument lets an honest .doc reach the API", async () => {
  const client = makeClient();
  const file = new File([OLE2], "policy.doc");
  await expect(client.uploadDocument("p1", file)).resolves.toBeDefined();
});
```

`makeClient()` is whatever helper the existing tests use to build an `ApiClient` with a mocked `fetch`; reuse it and extend its mock to answer the register/upload/parse calls, matching the existing `uploadDocument` success test.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/rmf-migrator/frontend && npx vitest run src/api/client.test.ts`
Expected: FAIL with `sniffUploadProblem is not exported`

- [ ] **Step 3: Write minimal implementation**

In `client.ts`, add below `sniffDocxProblem` (leaving that function untouched so its existing tests keep passing):

```ts
/**
 * Decide whether a chosen file may be uploaded, from its bytes and its name.
 *
 * OLE2 bytes are legitimate when the file is honestly named .doc — the backend
 * converts those, and is the single source of truth for whether conversion is
 * enabled in this environment. The same bytes under a .docx name are a renamed
 * file, which is a real user error worth naming here rather than shipping to a
 * server that will only reject it.
 */
export function sniffUploadProblem(head: Uint8Array, filename: string): string | null {
  if (head.length === 0) return "file is empty";
  if (filename.toLowerCase().endsWith(".doc")) {
    const ole2 = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1];
    if (ole2.every((b, i) => head[i] === b)) return null;
    if (head[0] === 0x50 && head[1] === 0x4b && head[2] === 0x03 && head[3] === 0x04) {
      return null; // a .docx misnamed .doc; the backend routes on bytes anyway
    }
  }
  return sniffDocxProblem(head);
}
```

Change `uploadDocument` to use it:

```ts
    const head = await readFileHead(file);
    const problem = sniffUploadProblem(head, file.name);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/rmf-migrator/frontend && npx vitest run src/api/client.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/rmf-migrator/frontend/src/api/client.ts apps/rmf-migrator/frontend/src/api/client.test.ts
git commit -m "feat(frontend): allow honestly-named .doc uploads through the sniff"
```

---

### Task 10: File picker and conversion badge

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/components/ProjectBrowser.tsx`
- Test: `frontend/src/components/ProjectBrowser.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/ProjectBrowser.test.tsx`:

```tsx
it("file input accepts .doc alongside .docx", async () => {
  renderBrowser();
  const input = await screen.findByLabelText(/upload a \.docx? policy document/i);
  expect(input.getAttribute("accept")).toContain(".doc");
  expect(input.getAttribute("accept")).toContain(".docx");
});

it("shows a converted badge for a document that arrived as .doc", async () => {
  renderBrowser({
    documents: [{ ...baseDocument, filename: "legacy.doc", source_format: "doc" }],
  });
  expect(await screen.findByText(/converted from \.doc/i)).toBeInTheDocument();
});

it("shows no badge for a native .docx", async () => {
  renderBrowser({ documents: [{ ...baseDocument, source_format: "docx" }] });
  await screen.findByText(baseDocument.filename);
  expect(screen.queryByText(/converted from \.doc/i)).not.toBeInTheDocument();
});
```

`renderBrowser` and `baseDocument` follow the existing helpers in this file; extend `baseDocument` with `source_format: "docx"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/rmf-migrator/frontend && npx vitest run src/components/ProjectBrowser.test.tsx`
Expected: FAIL — `accept` is `.docx` only and no badge renders

- [ ] **Step 3: Write minimal implementation**

In `types.ts`, add to `DocumentRecord`:

```ts
  source_format?: "docx" | "doc";
  converted_s3_key?: string | null;
```

In `ProjectBrowser.tsx`, change the file input:

```tsx
                  accept=".docx,.doc"
```

and update its `aria-label` to `"upload a .docx or .doc policy document"`. Render the badge next to the filename in the document row:

```tsx
                {doc.source_format === "doc" && (
                  <span className="doc-converted-badge" title="Uploaded as a legacy .doc and converted to .docx; formatting comes from that conversion">
                    converted from .doc
                  </span>
                )}
```

Add a muted style for `.doc-converted-badge` alongside the existing row styles, matching the surrounding badge conventions in the stylesheet.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/rmf-migrator/frontend && npx vitest run`
Expected: PASS — full frontend suite

- [ ] **Step 5: Commit**

```bash
git add apps/rmf-migrator/frontend/src
git commit -m "feat(frontend): accept .doc uploads and flag converted documents"
```

---

### Task 11: Converter Lambda handler and image

**Files:**
- Create: `backend/src/rmf_migrator/converter_lambda/__init__.py`, `handler.py`, `backend/converter.Dockerfile`
- Test: `backend/tests/test_converter_lambda.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_converter_lambda.py`:

```python
"""Tests for the LibreOffice converter Lambda handler.

soffice itself is stubbed: what is under test is the guard/cleanup logic
around it, which is where a mistake becomes a security problem.
"""

from __future__ import annotations

import pytest

from rmf_migrator.converter_lambda.handler import ConverterError, convert_bytes


def _fake_soffice(output: bytes):
    def run(data: bytes, workdir: str) -> bytes:
        return output

    return run


def test_convert_bytes_returns_soffice_output():
    docx = b"PK\x03\x04converted"
    assert convert_bytes(b"\xd0\xcf\x11\xe0doc", run=_fake_soffice(docx)) == docx


def test_convert_bytes_rejects_input_that_is_not_ole2():
    with pytest.raises(ConverterError):
        convert_bytes(b"PK\x03\x04already a docx", run=_fake_soffice(b"PK\x03\x04x"))


def test_convert_bytes_rejects_oversized_input():
    from rmf_migrator.common.limits import MAX_DOC_BYTES

    with pytest.raises(ConverterError):
        convert_bytes(
            b"\xd0\xcf\x11\xe0" + b"x" * MAX_DOC_BYTES, run=_fake_soffice(b"PK\x03\x04x")
        )


def test_convert_bytes_rejects_oversized_output():
    from rmf_migrator.common.limits import MAX_DOCX_BYTES

    oversized = b"PK\x03\x04" + b"x" * MAX_DOCX_BYTES
    with pytest.raises(ConverterError):
        convert_bytes(b"\xd0\xcf\x11\xe0doc", run=_fake_soffice(oversized))


def test_convert_bytes_rejects_output_that_is_not_a_docx():
    with pytest.raises(ConverterError):
        convert_bytes(b"\xd0\xcf\x11\xe0doc", run=_fake_soffice(b"%PDF-1.7"))


def test_convert_bytes_strips_a_macro_project_from_the_output():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("word/document.xml", b"<w:document/>")
        archive.writestr("word/vbaProject.bin", b"macro payload")

    result = convert_bytes(b"\xd0\xcf\x11\xe0doc", run=_fake_soffice(buf.getvalue()))

    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert "word/vbaProject.bin" not in archive.namelist()
        assert "word/document.xml" in archive.namelist()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_converter_lambda.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rmf_migrator.converter_lambda'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/src/rmf_migrator/converter_lambda/__init__.py` (empty) and `handler.py`:

```python
"""LibreOffice .doc -> .docx converter, running in its own container image.

This function is the only place untrusted OLE2 bytes are parsed. It gets its
own Lambda, its own minimal role, and no network egress, so a LibreOffice
vulnerability cannot reach the API, the database, or Bedrock.

Guards mirror common/limits.py: bounded input, bounded output, and a format
check on both ends. Macros never execute during a headless convert; the
vbaProject strip is defense in depth for the artifact we hand downstream.
"""

from __future__ import annotations

import io
import json
import os
import subprocess  # noqa: S404 — fixed argv, no shell, no user-controlled path
import tempfile
import zipfile
from collections.abc import Callable

from rmf_migrator.common.limits import MAX_DOC_BYTES, MAX_DOCX_BYTES, sniff_document_format

_TIMEOUT_SECONDS = 60


class ConverterError(Exception):
    """Conversion could not produce a safe, in-bounds .docx."""


def _run_soffice(data: bytes, workdir: str) -> bytes:
    source = os.path.join(workdir, "input.doc")
    with open(source, "wb") as handle:
        handle.write(data)
    try:
        subprocess.run(  # noqa: S603 — argv list, shell=False, paths are ours
            [
                "soffice",
                "--headless",
                "--norestore",
                "--convert-to",
                "docx",
                "--outdir",
                workdir,
                source,
            ],
            check=True,
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConverterError("conversion timed out") from exc
    except subprocess.CalledProcessError as exc:
        # stderr can echo document content; report failure, never the text.
        raise ConverterError("conversion process failed") from exc

    produced = os.path.join(workdir, "input.docx")
    if not os.path.exists(produced):
        raise ConverterError("conversion produced no output")
    with open(produced, "rb") as handle:
        return handle.read()


def _strip_macros(data: bytes) -> bytes:
    """Rewrite the archive without any VBA project."""
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        names = source.namelist()
        if not any(name.endswith("vbaProject.bin") for name in names):
            return data
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                if info.filename.endswith("vbaProject.bin"):
                    continue
                target.writestr(info, source.read(info.filename))
        return out.getvalue()


def convert_bytes(data: bytes, *, run: Callable[[bytes, str], bytes] = _run_soffice) -> bytes:
    if len(data) > MAX_DOC_BYTES:
        raise ConverterError(f"input exceeds {MAX_DOC_BYTES} bytes")
    if sniff_document_format(data) != "doc":
        raise ConverterError("input is not a legacy binary .doc")

    with tempfile.TemporaryDirectory() as workdir:
        converted = run(data, workdir)

    if len(converted) > MAX_DOCX_BYTES:
        raise ConverterError(f"output exceeds {MAX_DOCX_BYTES} bytes")
    if sniff_document_format(converted) != "docx":
        raise ConverterError("output is not a .docx")
    return _strip_macros(converted)


def handler(event: dict, _context: object = None) -> dict:
    """Read source_key, convert, write target_key. Payload carries keys only."""
    import boto3

    s3 = boto3.client("s3")
    bucket = event["bucket"]
    body = s3.get_object(Bucket=bucket, Key=event["source_key"])["Body"]
    try:
        data = body.read(MAX_DOC_BYTES + 1)
    finally:
        body.close()

    converted = convert_bytes(data)
    s3.put_object(
        Bucket=bucket,
        Key=event["target_key"],
        Body=converted,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=os.environ["KMS_KEY_ID"],
        ContentType=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )
    return json.loads(json.dumps({"ok": True}))
```

Create `backend/converter.Dockerfile`:

```dockerfile
# LibreOffice converter Lambda. Pinned by digest so the image cannot drift —
# see the supply-chain rule in the repo's security posture.
FROM public.ecr.aws/lambda/python:3.12

RUN dnf install -y libreoffice-writer && dnf clean all

COPY src/rmf_migrator ${LAMBDA_TASK_ROOT}/rmf_migrator
RUN pip install --no-cache-dir pydantic boto3

# soffice needs a writable HOME for its per-user profile; /tmp is the only
# writable path in a Lambda container.
ENV HOME=/tmp

CMD ["rmf_migrator.converter_lambda.handler.handler"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_converter_lambda.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Pin the base image by digest**

Resolve and pin the digest so the build is reproducible:

```bash
docker pull public.ecr.aws/lambda/python:3.12 && docker inspect --format='{{index .RepoDigests 0}}' public.ecr.aws/lambda/python:3.12
```

Replace the `FROM` line with the `image@sha256:...` form the command prints.

- [ ] **Step 6: Commit**

```bash
git add apps/rmf-migrator/backend/src/rmf_migrator/converter_lambda apps/rmf-migrator/backend/converter.Dockerfile apps/rmf-migrator/backend/tests/test_converter_lambda.py
git commit -m "feat(converter): add LibreOffice converter Lambda handler and image"
```

---

### Task 12: Terraform behind a feature flag

**Files:**
- Create: `terraform/modules/rmf-migrator/converter.tf`
- Modify: `terraform/modules/rmf-migrator/variables.tf`, `iam.tf`, and the Lambda env blocks for API + worker

- [ ] **Step 1: Add the flag**

In `variables.tf`:

```hcl
variable "enable_doc_conversion" {
  description = <<-EOT
    Deploy the LibreOffice converter Lambda so legacy binary .doc uploads are
    converted server-side. Off by default: LibreOffice lands inside the
    accreditation boundary and needs ISSO approval plus its own patch and scan
    coverage. See docs/superpowers/specs/2026-08-07-doc-conversion-design.md.
  EOT
  type        = bool
  default     = false
}
```

- [ ] **Step 2: Create the converter resources**

In `converter.tf`, define, each with `count = var.enable_doc_conversion ? 1 : 0`:

1. `aws_ecr_repository.converter` with `image_scanning_configuration { scan_on_push = true }`
2. `aws_iam_role.converter` using the existing `data.aws_iam_policy_document.lambda_assume`
3. `aws_iam_role_policy.converter` granting **only**:
   - `s3:GetObject` on `"${aws_s3_bucket.documents.arn}/convert-scratch/*"`
   - `s3:PutObject` on `"${aws_s3_bucket.documents.arn}/convert-scratch/*"`
   - `kms:Decrypt`, `kms:GenerateDataKey` on the documents CMK
   - CloudWatch Logs create/put on its own log group
   No DynamoDB, no Bedrock, no SQS, no access outside the scratch prefix.
4. `aws_lambda_function.converter` with `package_type = "Image"`, `timeout = 120`, `memory_size = 2048`, `environment { variables = { KMS_KEY_ID = aws_kms_key.documents.id } }`, and **no** `vpc_config` (no egress path)
5. `aws_cloudwatch_log_group.converter` with the same retention as the other functions

Match the naming, tagging, and log-retention conventions already used in `lambdas.tf`.

- [ ] **Step 3: Wire the env vars and invoke permission**

Add to the API and worker Lambda `environment.variables` blocks:

```hcl
      DOC_CONVERSION_BACKEND      = var.enable_doc_conversion ? "lambda" : "reject"
      DOC_CONVERTER_FUNCTION_NAME = var.enable_doc_conversion ? aws_lambda_function.converter[0].function_name : ""
```

In `iam.tf`, add to the worker role policy, guarded by the same flag:

```hcl
  dynamic "statement" {
    for_each = var.enable_doc_conversion ? [1] : []
    content {
      actions   = ["lambda:InvokeFunction"]
      resources = [aws_lambda_function.converter[0].arn]
    }
  }
```

The worker also needs `s3:PutObject`/`s3:GetObject`/`s3:DeleteObject` on `convert-scratch/*` — extend its existing documents-bucket statement to cover that prefix.

- [ ] **Step 4: Validate**

Run: `cd apps/rmf-migrator/terraform && terraform init -backend=false && terraform validate`
Expected: `Success! The configuration is valid.`

Then confirm the default plan is a no-op for conversion:

Run: `terraform plan -var-file=<your env tfvars> | grep -ci converter`
Expected: `0` — with the flag off, nothing converter-related is planned.

- [ ] **Step 5: Commit**

```bash
git add apps/rmf-migrator/terraform
git commit -m "feat(terraform): add converter Lambda behind enable_doc_conversion flag"
```

---

### Task 13: Fidelity fixture, docs, and full verification

The spec's constraint 2 is the one a passing unit suite would not catch: if conversion drops heading styles, every document silently becomes one unsectioned blob.

**Files:**
- Create: `backend/tests/fixtures/legacy_policy.doc`, `backend/tests/fixtures/legacy_policy.converted.docx`
- Modify: `backend/tests/test_convert_ingest.py`, `docs/USER_MANUAL.md`, `README.md`

- [ ] **Step 1: Build the fixture pair**

Create a small Word 97-2003 `.doc` with a `Heading 1`, a `Heading 2` beneath it, and a body paragraph under each. Convert it once with LibreOffice and commit both files:

```bash
soffice --headless --convert-to docx --outdir apps/rmf-migrator/backend/tests/fixtures apps/rmf-migrator/backend/tests/fixtures/legacy_policy.doc
```

Rename the output to `legacy_policy.converted.docx`. Add `*.doc binary` to `apps/rmf-migrator/.gitattributes` next to the existing `*.docx binary` line.

- [ ] **Step 2: Write the fidelity test**

Append to `backend/tests/test_convert_ingest.py`:

```python
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_conversion_preserves_heading_structure():
    """The whole feature rests on this: if conversion loses heading styles,
    every document collapses into one unsectioned blob and the mapping
    workflow silently degrades."""
    converted = (FIXTURES / "legacy_policy.converted.docx").read_bytes()
    sections = parse_docx_bytes(converted, document_id="doc_1", project_id="proj_1")

    levels = [s.level for s in sections if s.heading]
    assert 1 in levels, "top-level heading did not survive conversion"
    assert 2 in levels, "nested heading did not survive conversion"
    assert any(s.text.strip() for s in sections), "body text did not survive conversion"
```

Add `from rmf_migrator.docx.parser import parse_docx_bytes` to the imports.

- [ ] **Step 3: Run it**

Run: `cd apps/rmf-migrator/backend && python -m pytest tests/test_convert_ingest.py::test_conversion_preserves_heading_structure -v`
Expected: PASS. If it fails, conversion fidelity is inadequate and the ISSO question is moot — stop and report before continuing.

- [ ] **Step 4: Update the docs**

In `docs/USER_MANUAL.md`, document that `.doc` uploads are converted when the deployment enables it, that the converted copy is what gets parsed and exported, and that the original is retained. In `README.md`, add `enable_doc_conversion` to the configuration section with a pointer to the spec.

- [ ] **Step 5: Full verification**

```bash
cd apps/rmf-migrator/backend && python -m pytest tests/ -v && ruff check src tests
cd ../frontend && npx vitest run && npx tsc --noEmit
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add apps/rmf-migrator
git commit -m "test(convert): assert heading structure survives conversion; document the flag"
```

---

## Definition of done

Unit tests are necessary but not sufficient. Before this is called complete:

1. Full backend + frontend suites green, `ruff` and `tsc` clean
2. `terraform validate` passes and the default plan contains no converter resources
3. **Live verification** — with `enable_doc_conversion = true` in a non-prod environment, upload a real legacy `.doc` through the browser and confirm: document reaches PARSED, sections carry correct heading levels, the badge renders, the original `.doc` object is unchanged in S3, and a Rev 5 export downloads and opens in Word
4. With the flag off, a `.doc` upload returns the "not enabled" message and no converter resources exist

Step 3 is the one that decides whether this shipped.

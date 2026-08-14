# Legacy .doc Import Support — Design

**Date:** 2026-08-07
**App:** apps/rmf-migrator
**Status:** Approved (design); implementation pending
**Open item:** ISSO decision on LibreOffice inside the accreditation boundary — see §7.

## Problem

The import pipeline accepts only zip-based `.docx`. Legacy binary `.doc` files
(OLE2, Word 97–2003) are rejected at three independent points:

- Frontend byte sniff: `frontend/src/api/client.ts` (OLE2 magic → reject before any API call)
- Backend byte sniff: `backend/src/rmf_migrator/common/limits.py` `sniff_docx_problem`
- Extension gate: `backend/src/rmf_migrator/handlers/request_upload.py` (`.docx` only)

The user's document set contains many legacy `.doc` policy documents and more
will keep arriving indefinitely. Manual re-saving in Word does not scale.

## Constraints

1. **Export surgery requires real .docx structure.** `docx/export_docx.py`
   rewrites the parsed file in place, preserving styles/TOC. A binary `.doc`
   cannot be written back to; the converted copy must become the document of
   record.
2. **Parsing keys off heading styles and `w:outlineLvl`** (`docx/parser.py`).
   Plain-text extraction from `.doc` would collapse documents into one
   unsectioned blob. Conversion must preserve heading styles/outline levels.
3. **Accreditation boundary.** Server-side conversion means LibreOffice
   (~1 GB container) inside the deployed AWS environment. The ISSO has not yet
   approved this. The design must deliver value and stay fully testable while
   that decision is pending, and slot the converter in without rework if
   approved.
4. **OLE2 is a macro-bearing format with a CVE history.** The converter is a
   new attack surface and needs its own input/output ceilings, timeout,
   isolation, and least-privilege IAM — the existing zip-bomb guards in
   `limits.py` do not apply to OLE2.

## Chosen approach (A): conversion seam + swappable backend

One narrow port; the pipeline routes OLE2 bytes through it instead of
rejecting them. The backend behind the port is a config choice. Parser and
exporter never learn `.doc` exists.

### 1. The seam

New module `backend/src/rmf_migrator/doc_convert/`:

```python
class DocConverter(Protocol):
    def convert(self, data: bytes) -> bytes:  # OLE2 .doc in, .docx out
        ...

class ConversionUnavailable(Exception):
    """Backend not deployed/enabled in this environment."""

class ConversionFailed(Exception):
    """Bad input, timeout, or over-limit output."""
```

Implementations:

- **`RejectingConverter`** — always raises `ConversionUnavailable` with the
  current "re-save in Word" guidance. Default backend; deployed behavior today
  is unchanged except the message can add "conversion is not enabled in this
  environment."
- **`LibreOfficeLambdaConverter`** — invokes a converter Lambda by function
  name from config. Code + tests ship now; infrastructure deploys only behind
  a Terraform `enable_doc_conversion` variable (default `false`).

Backend selection: one env var read in `Deps.build()`. Flipping backends is a
config change, not a release.

### 2. Ingest flow changes

1. **`request_upload.py`** — accept `.doc` filenames when conversion is
   enabled. Response carries a per-extension presigned POST: a `.doc` variant
   of the POST policy in `common/storage.py` pins `Content-Type:
   application/msword` and a tighter 15 MB `content-length-range`. When
   conversion is disabled, `.doc` uploads get a 400 with the
   `ConversionUnavailable` guidance — the user never waits on a doomed job.
2. **Worker (`handlers/parse_document.py`)** — sniff bytes before parsing:
   - OLE2 magic → `convert()` → write converted bytes to a **new S3 key**
     `projects/{project_id}/documents/{document_id}.converted.docx` → run the
     existing docx guards and parse the converted bytes.
   - Zip magic → parse as today.
   - The original `.doc` object is never modified or deleted: audit provenance.
3. **Document model (`common/models.py`)** — new fields:
   - `source_format: Literal["docx", "doc"]` (default `"docx"`)
   - `converted_s3_key: str | None`
   Export surgery and any re-parse read `converted_s3_key` when present.
4. **Frontend (`api/client.ts`, `components/ProjectBrowser.tsx`)** — the OLE2
   sniff becomes "allowed, tagged for conversion" when the API reports
   conversion enabled; remains a rejection otherwise. File input `accept`
   gains `.doc`. Document rows with `source_format == "doc"` show a
   "converted from .doc" badge so reviewers know formatting derives from a
   conversion.

HTML and PDF impostors remain rejected at every layer, unchanged.

### 3. Converter Lambda (built now, deployed later)

- Container-image Lambda running `soffice --headless --convert-to docx`.
- Macros never execute: LibreOffice does not run VBA during conversion, and
  any `vbaProject.bin` is stripped from the output as belt-and-suspenders.
- Guards (mirroring `limits.py` philosophy):
  - input cap 15 MB, output cap 25 MB (`MAX_DOCX_BYTES`) — then the normal
    `guard_docx_bytes` runs on the result in the worker
  - 60 s hard timeout
  - own IAM role, scoped to read/write only the project documents prefix
    (following the split-role pattern in `terraform/modules/rmf-migrator/iam.tf`)
  - no network egress
- All infrastructure behind `enable_doc_conversion` (default `false`).

### 4. Error handling

- `ConversionUnavailable` → HTTP 400 at upload registration time.
- `ConversionFailed` → job `FAILED`, document `FAILED` with
  `failure_stage="convert"`, error **type** only — never content — matching
  the existing rule in `parse_document.py`.
- Converted output that fails `guard_docx_bytes` → `ConversionFailed`.

### 5. Testing

- Unit: sniff-routing (OLE2 → convert, zip → parse, impostors → reject);
  both converters, `LibreOfficeLambdaConverter` mocked at the Lambda-invoke
  boundary.
- Worker: convert-then-parse path, provenance keys (original untouched,
  converted key recorded), `failure_stage="convert"` on failure.
- Frontend: mirror the existing impostor suite for the enabled/disabled
  states; badge rendering.
- Storage: moto test for the `.doc` POST policy (content-type pin, 15 MB cap).
- **Fidelity fixture:** a styled `.doc` fixture converted ahead of time,
  asserting section structure (headings, nesting) survives conversion — this
  guards constraint 2.

### 6. Explicitly out of scope

- `.rtf`, `.odt`, `.pdf` ingestion.
- Writing Rev 5 output back to `.doc` format (export remains `.docx` only).
- Batch/bulk upload tooling (may be a follow-on).

### 7. Open item — ISSO decision

LibreOffice in a container-image Lambda lands inside the accreditation
boundary: new software inventory, patch cadence, and scan surface. Until the
ISSO rules:

- Deployed behavior = `RejectingConverter` (identical to today's rejection,
  clearer message).
- If **approved**: set `enable_doc_conversion = true`, deploy the converter
  Lambda, flip the backend env var. No pipeline rework.
- If **declined**: the seam stays; a future out-of-boundary conversion
  service can implement the same port. The `RejectingConverter` message is
  the permanent user-facing answer in-app.

## Alternatives considered

- **B — workstation batch converter only:** zero boundary impact, but fails
  the "ongoing arrival" requirement; every future user must know to run it.
- **C — build and deploy LibreOffice Lambda now:** fastest to end state, but
  gambles the container/Terraform/scan investment on an unmade ISSO decision.

"""SQS worker — parse an uploaded .docx into sections.

Message shape: ``{"job_id", "project_id", "document_id"}``.

Flow per message:
  1. Mark job RUNNING and document PARSING.
  2. Fetch bytes from S3, convert a legacy .doc if that is what they are,
     then parse into sections.
  3. Persist sections; mark document PARSED (with count) and job SUCCEEDED.
On failure, mark both FAILED with an error *type* only — never content.

The core is ``run_parse_job``. The SQS entrypoint that dispatches parse vs.
mapping messages lives in ``handlers/worker.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from rmf_migrator.common.limits import (
    DocxTooLarge,
    ObjectTooLarge,
    ParsedDocumentTooLarge,
    UnsupportedDocumentFormat,
    sniff_document_format,
)
from rmf_migrator.common.logging import length_of, log_error, log_event
from rmf_migrator.common.models import Document, DocumentStatus, JobStatus
from rmf_migrator.common.sections import externalize_large_section_texts
from rmf_migrator.common.storage import build_converted_document_key
from rmf_migrator.doc_convert import ConversionError
from rmf_migrator.docx.parser import parse_docx_bytes
from rmf_migrator.handlers.deps import Deps

# Deterministic verdicts on the stored bytes: identical on every redelivery,
# so an SQS retry can only burn the redrive budget and land in the DLQ.
_PERMANENT_PARSE_ERRORS = (
    DocxTooLarge,
    ObjectTooLarge,
    ParsedDocumentTooLarge,
    UnsupportedDocumentFormat,
)


def _log_format_correction(document: Document, sniffed: str) -> None:
    """Announce a record correction before it is applied.

    The worker rewriting a document's format is otherwise invisible: only
    ``document.registered`` carries source_format, and it recorded the
    extension's claim. Without this the conversions that arrived under a .docx
    name are countable from no log at all, leaving the DynamoDB row — state
    that dies with the project — as the sole record.
    """
    log_event(
        "document.format_corrected",
        project_id=document.project_id,
        document_id=document.document_id,
        registered_format=document.source_format,
        sniffed_format=sniffed,
    )


def _ensure_docx(data: bytes, document: Document, deps: Deps) -> bytes:
    """Return parseable .docx bytes, converting a legacy .doc if needed.

    Routing is decided by magic bytes, not the filename: a mislabeled upload
    must be handled by what it actually is. The converted copy is written to
    its own key so the uploaded original stays byte-identical for audit.

    request_upload derives ``source_format`` from the extension, so a
    mislabeled upload arrives here on record as the format it is not. Both
    directions of that mismatch are corrected to what the bytes say, because
    the bytes are what this pipeline acted on and the record is read as if it
    described them: "doc" with no converted key strands the document, since
    parse_key refuses it and export dies after a clean parse, while "docx" on
    a document that was in fact converted hides the conversion from the
    reviewers whose UI badge reads this field.

    ``converted_s3_key`` is the other half of that provenance, so this function
    owns both fields end to end rather than leaving one to its caller. It
    retires with the format: bytes that route native were not produced by any
    conversion, and parse_key prefers a converted key whenever one is set, so a
    key left over from an earlier attempt on since-replaced bytes would keep
    resolving — and export would serve a document that was never parsed,
    mapped, or reviewed.
    """
    sniffed = sniff_document_format(data)
    if sniffed != "doc":
        if sniffed == "docx" and document.source_format != "docx":
            _log_format_correction(document, "docx")
            document.source_format = "docx"
        document.converted_s3_key = None
        return data

    if document.source_format != "doc":
        _log_format_correction(document, "doc")
    # Both fields move before the conversion runs, not after. A failure must not
    # leave raw OLE2 bytes on record as native, where parse_key would hand them
    # straight to the parser and the exporter; nor may it leave an earlier
    # attempt's key on record, since that key is deterministic and would still
    # resolve — to an object holding the output of converting superseded bytes.
    document.source_format = "doc"
    document.converted_s3_key = None
    converted = deps.converter.convert(data)
    key = build_converted_document_key(document.project_id, document.document_id)
    deps.store.put_bytes(key, converted)
    document.converted_s3_key = key
    return converted


def run_parse_job(project_id: str, document_id: str, job_id: str, deps: Deps) -> bool:
    existing_job = deps.repo.get_job(project_id, job_id)
    document = deps.repo.get_document(project_id, document_id)
    if existing_job is None or document is None:
        # Nothing to update; log and drop (message will not be retried usefully).
        log_event(
            "parse.job_missing",
            project_id=project_id,
            document_id=document_id,
            job_id=job_id,
            job_found=existing_job is not None,
            document_found=document is not None,
        )
        return False

    if existing_job.document_id != document_id:
        log_event("parse.job_document_mismatch", project_id=project_id, job_id=job_id)
        return False
    if existing_job.status == JobStatus.SUCCEEDED:
        # A prior attempt may have parsed successfully but failed while chaining
        # the mapping message. Allow process_event to retry only that handoff.
        return document.status == DocumentStatus.PARSED
    if document.status == DocumentStatus.PARSED:
        # Recover a timeout that happened after the document write but before
        # the terminal job write; retry only the parse -> mapping handoff.
        existing_job.status = JobStatus.SUCCEEDED
        existing_job.error_type = None
        existing_job.updated_at = datetime.now(UTC)
        deps.repo.put_job(existing_job)
        return True

    job = deps.repo.claim_job(project_id, job_id)
    if job is None:
        return False

    # "convert" is an upstream sub-stage of this same worker, so re-running from
    # it is as safe as re-running from "parse" — and for ConversionUnavailable
    # the fix *is* enabling the backend and letting SQS redeliver. Downstream
    # stages stay refused: a mapping job may still be in flight over sections a
    # re-parse would rewrite underneath it.
    if document.status not in {
        DocumentStatus.UPLOADED,
        DocumentStatus.PARSING,
        DocumentStatus.FAILED,
    } or (
        document.status == DocumentStatus.FAILED
        and document.failure_stage not in {"parse", "convert"}
    ):
        job.status = JobStatus.FAILED
        job.error_type = "InvalidDocumentState"
        job.updated_at = datetime.now(UTC)
        deps.repo.put_job(job)
        return False

    document.status = DocumentStatus.PARSING
    document.failure_stage = None
    deps.repo.put_document(document)

    try:
        data = deps.store.get_bytes(document.s3_key)
        parse_bytes = _ensure_docx(data, document, deps)
        sections = parse_docx_bytes(parse_bytes, document_id=document_id, project_id=project_id)
        externalize_large_section_texts(sections, deps.store)
        deps.repo.put_sections(sections)

        document.status = DocumentStatus.PARSED
        document.section_count = len(sections)
        document.parse_error = None
        document.failure_stage = None
        document.active_job_id = None
        deps.repo.put_document(document)

        job.status = JobStatus.SUCCEEDED
        job.error_type = None
        job.updated_at = datetime.now(UTC)
        deps.repo.put_job(job)

        total_chars = sum(s.char_length for s in sections)
        log_event(
            "document.parsed",
            project_id=project_id,
            document_id=document_id,
            job_id=job_id,
            section_count=len(sections),
            char_length=length_of("x" * total_chars),  # report size, not text
        )
        return True
    except Exception as exc:  # noqa: BLE001 — worker must record failure, not crash silently
        document.status = DocumentStatus.FAILED
        document.parse_error = type(exc).__name__
        document.failure_stage = "convert" if isinstance(exc, ConversionError) else "parse"
        document.active_job_id = None
        deps.repo.put_document(document)

        job.status = JobStatus.FAILED
        job.error_type = type(exc).__name__
        job.updated_at = datetime.now(UTC)
        deps.repo.put_job(job)

        log_error("document.parse_failed", exc, project_id=project_id, document_id=document_id)
        if isinstance(exc, _PERMANENT_PARSE_ERRORS):
            # Fully recorded above; end the message so it is not redelivered.
            return False
        raise  # transient (S3/KMS/DynamoDB) — let SQS/Lambda retry

"""SQS worker — parse an uploaded .docx into sections.

Message shape: ``{"job_id", "project_id", "document_id"}``.

Flow per message:
  1. Mark job RUNNING and document PARSING.
  2. Fetch bytes from S3, parse into sections.
  3. Persist sections; mark document PARSED (with count) and job SUCCEEDED.
On failure, mark both FAILED with an error *type* only — never content.

The core is ``run_parse_job``. The SQS entrypoint that dispatches parse vs.
mapping messages lives in ``handlers/worker.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from rmf_migrator.common.logging import length_of, log_error, log_event
from rmf_migrator.common.models import DocumentStatus, JobStatus
from rmf_migrator.common.sections import externalize_large_section_texts
from rmf_migrator.docx.parser import parse_docx_bytes
from rmf_migrator.handlers.deps import Deps


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

    if document.status not in {
        DocumentStatus.UPLOADED,
        DocumentStatus.PARSING,
        DocumentStatus.FAILED,
    } or (document.status == DocumentStatus.FAILED and document.failure_stage != "parse"):
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
        sections = parse_docx_bytes(data, document_id=document_id, project_id=project_id)
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
        document.failure_stage = "parse"
        document.active_job_id = None
        deps.repo.put_document(document)

        job.status = JobStatus.FAILED
        job.error_type = type(exc).__name__
        job.updated_at = datetime.now(UTC)
        deps.repo.put_job(job)

        log_error("document.parse_failed", exc, project_id=project_id, document_id=document_id)
        raise  # let SQS/Lambda apply its retry + DLQ policy

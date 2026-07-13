"""SQS worker — parse an uploaded .docx into sections.

Message shape: ``{"job_id", "project_id", "document_id"}``.

Flow per message:
  1. Mark job RUNNING and document PARSING.
  2. Fetch bytes from S3, parse into sections.
  3. Persist sections; mark document PARSED (with count) and job SUCCEEDED.
On failure, mark both FAILED with an error *type* only — never content.

The core is ``run_parse_job`` so tests can drive it directly with fakes.
"""

from __future__ import annotations

import json
from typing import Any

from rmf_migrator.common.logging import length_of, log_error, log_event
from rmf_migrator.common.models import DocumentStatus, JobStatus
from rmf_migrator.docx.parser import parse_docx_bytes
from rmf_migrator.handlers.deps import Deps


def run_parse_job(project_id: str, document_id: str, job_id: str, deps: Deps) -> None:
    job = deps.repo.get_job(project_id, job_id)
    document = deps.repo.get_document(project_id, document_id)
    if job is None or document is None:
        # Nothing to update; log and drop (message will not be retried usefully).
        log_event(
            "parse.job_missing",
            project_id=project_id,
            document_id=document_id,
            job_id=job_id,
            job_found=job is not None,
            document_found=document is not None,
        )
        return

    job.status = JobStatus.RUNNING
    deps.repo.put_job(job)
    document.status = DocumentStatus.PARSING
    deps.repo.put_document(document)

    try:
        data = deps.store.get_bytes(document.s3_key)
        sections = parse_docx_bytes(data, document_id=document_id, project_id=project_id)
        deps.repo.put_sections(sections)

        document.status = DocumentStatus.PARSED
        document.section_count = len(sections)
        document.parse_error = None
        deps.repo.put_document(document)

        job.status = JobStatus.SUCCEEDED
        job.error_type = None
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
    except Exception as exc:  # noqa: BLE001 — worker must record failure, not crash silently
        document.status = DocumentStatus.FAILED
        document.parse_error = type(exc).__name__
        deps.repo.put_document(document)

        job.status = JobStatus.FAILED
        job.error_type = type(exc).__name__
        deps.repo.put_job(job)

        log_error("document.parse_failed", exc, project_id=project_id, document_id=document_id)
        raise  # let SQS/Lambda apply its retry + DLQ policy


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """SQS batch handler with partial-batch-failure reporting."""
    deps = Deps.build()
    failures: list[dict[str, str]] = []

    for record in event.get("Records", []):
        try:
            body = json.loads(record["body"])
            run_parse_job(
                project_id=body["project_id"],
                document_id=body["document_id"],
                job_id=body["job_id"],
                deps=deps,
            )
        except Exception:  # noqa: BLE001
            failures.append({"itemIdentifier": record.get("messageId", "")})

    return {"batchItemFailures": failures}

"""POST /projects/{project_id}/documents/{document_id}/parse — start async parse.

Called by the client after it has finished PUTting the .docx to S3. Creates a
ParseJob (pending) and enqueues an SQS message for the worker. Returns the
job_id the client polls.
"""

from __future__ import annotations

import json
from typing import Any

from botocore.exceptions import ClientError

from rmf_migrator.common.http import (
    HttpError,
    error_response,
    json_response,
    path_param,
)
from rmf_migrator.common.limits import MAX_DOCX_BYTES
from rmf_migrator.common.logging import log_event
from rmf_migrator.common.models import DocumentStatus, JobStatus, ParseJob
from rmf_migrator.handlers.deps import Deps


def _enqueue(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id = path_param(event, "project_id")
    document_id = path_param(event, "document_id")

    document = deps.repo.get_document(project_id, document_id)
    if document is None:
        raise HttpError(404, "document not found")
    if document.status == DocumentStatus.PARSING and document.active_job_id:
        existing = deps.repo.get_job(project_id, document.active_job_id)
        if existing is not None:
            return json_response(202, {"job": existing.model_dump()})

    allowed = {DocumentStatus.UPLOAD_PENDING, DocumentStatus.UPLOADED}
    if document.status == DocumentStatus.FAILED and document.failure_stage == "parse":
        allowed.add(DocumentStatus.FAILED)
    if document.status not in allowed:
        raise HttpError(409, f"document cannot be parsed from status {document.status}")

    try:
        metadata = deps.store.head(document.s3_key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            raise HttpError(409, "document upload is not complete") from exc
        raise
    if int(metadata.get("ContentLength", 0)) > MAX_DOCX_BYTES:
        raise HttpError(413, f"document exceeds the {MAX_DOCX_BYTES}-byte upload limit")

    prior_status = document.status
    prior_failure_stage = document.failure_stage
    prior_parse_error = document.parse_error
    job = ParseJob(job_id=f"job_{document_id}", project_id=project_id, document_id=document_id)
    document.status = DocumentStatus.PARSING
    document.failure_stage = None
    document.parse_error = None
    document.active_job_id = job.job_id
    if not deps.repo.put_document_if_status(document, allowed):
        raise HttpError(409, "document state changed; refresh and try again")

    try:
        deps.repo.put_job(job)
        deps.sqs.send_message(
            QueueUrl=deps.config.parse_queue_url,
            MessageBody=json.dumps(
                {
                    "kind": "parse",
                    "job_id": job.job_id,
                    "project_id": project_id,
                    "document_id": document_id,
                }
            ),
        )
    except Exception:
        # Restore the full prior state: losing failure_stage would leave a
        # FAILED document that the retry gate above can never re-admit.
        document.status = prior_status
        document.failure_stage = prior_failure_stage
        document.parse_error = prior_parse_error
        document.active_job_id = None
        deps.repo.put_document(document)
        job.status = JobStatus.FAILED
        job.error_type = "EnqueueFailed"
        deps.repo.put_job(job)
        raise

    log_event(
        "parse.enqueued",
        project_id=project_id,
        document_id=document_id,
        job_id=job.job_id,
    )
    return json_response(202, {"job": job.model_dump()})


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _enqueue(event, Deps.build())
    except HttpError as err:
        return error_response(err)

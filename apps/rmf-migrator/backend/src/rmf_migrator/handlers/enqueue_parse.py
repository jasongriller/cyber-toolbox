"""POST /projects/{project_id}/documents/{document_id}/parse — start async parse.

Called by the client after it has finished PUTting the .docx to S3. Creates a
ParseJob (pending) and enqueues an SQS message for the worker. Returns the
job_id the client polls.
"""

from __future__ import annotations

import json
from typing import Any

from rmf_migrator.common.http import (
    HttpError,
    error_response,
    json_response,
    path_param,
)
from rmf_migrator.common.logging import log_event
from rmf_migrator.common.models import DocumentStatus, ParseJob
from rmf_migrator.handlers.deps import Deps


def _enqueue(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id = path_param(event, "project_id")
    document_id = path_param(event, "document_id")

    document = deps.repo.get_document(project_id, document_id)
    if document is None:
        raise HttpError(404, "document not found")
    if document.status == DocumentStatus.PARSING:
        raise HttpError(409, "document is already being parsed")

    job = ParseJob(project_id=project_id, document_id=document_id)
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

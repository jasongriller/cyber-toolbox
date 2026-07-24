"""POST /projects/{project_id}/documents — register a document and get an upload URL.

Creates the Document record (status=uploaded is set only after the client PUTs;
here it starts as uploaded-pending via the returned presigned URL) and returns a
presigned S3 PUT URL. Bytes go browser -> S3 directly; no document content
touches this Lambda.
"""

from __future__ import annotations

from typing import Any

from rmf_migrator.common.http import (
    HttpError,
    error_response,
    json_response,
    parse_body,
    path_param,
    resolve_identity,
)
from rmf_migrator.common.logging import log_event
from rmf_migrator.common.models import Document
from rmf_migrator.common.storage import build_document_key
from rmf_migrator.handlers.deps import Deps

_MAX_FILENAME = 260


def _request_upload(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id = path_param(event, "project_id")
    project = deps.repo.get_project(project_id)
    if project is None:
        raise HttpError(404, "project not found")

    body = parse_body(event)
    filename = (body.get("filename") or "").strip()
    if not filename:
        raise HttpError(400, "'filename' is required")
    if len(filename) > _MAX_FILENAME:
        raise HttpError(400, "'filename' too long")
    if not filename.lower().endswith(".docx"):
        raise HttpError(400, "only .docx files are supported in v1")

    identity = resolve_identity(event, deps.config.identity_header)
    document = Document(
        project_id=project_id,
        filename=filename,
        s3_key="",  # set below once we have the id
        uploaded_by=identity,
    )
    document.s3_key = build_document_key(project_id, document.document_id, filename)
    deps.repo.put_document(document)
    deps.repo.increment_document_count(project_id)

    upload = deps.store.presigned_put_url(document.s3_key)

    log_event(
        "document.registered",
        project_id=project_id,
        document_id=document.document_id,
        uploaded_by=identity,
    )
    return json_response(
        201,
        {"document": document.model_dump(), "upload": upload},
    )


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _request_upload(event, Deps.build())
    except HttpError as err:
        return error_response(err)

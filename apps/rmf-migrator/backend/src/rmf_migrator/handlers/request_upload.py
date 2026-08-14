"""POST /projects/{project_id}/documents — register a document and get an upload target.

Creates the Document record (status=uploaded is set only after the client
uploads; here it starts as uploaded-pending) and returns a presigned S3 POST
target whose policy pins CMK encryption and a content-length-range size
ceiling. Bytes go browser -> S3 directly; no document content touches this
Lambda.
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
from rmf_migrator.doc_convert import CONVERSION_DISABLED_MESSAGE
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
    # Quotes, backslashes, and control characters would break out of the
    # Content-Disposition header the download path builds from this name.
    if '"' in filename or "\\" in filename or any(not c.isprintable() for c in filename):
        raise HttpError(400, "'filename' contains unsupported characters")
    lowered = filename.lower()
    if lowered.endswith(".doc"):
        # An honestly named .doc is turned away here, before it can occupy a
        # .doc key or reach a converter that this environment does not have.
        # That is an extension gate, not a content guarantee: registration
        # never sees bytes, so a renamed .doc still lands in the bucket under
        # a .docx key. Rejecting on content stays guard_docx_bytes' job in the
        # parser, the same split parse_key documents for the mirror case.
        if not deps.config.conversion_enabled:
            raise HttpError(400, CONVERSION_DISABLED_MESSAGE)
        source_format = "doc"
    elif lowered.endswith(".docx"):
        source_format = "docx"
    else:
        raise HttpError(400, "only .docx and .doc files are supported")

    identity = resolve_identity(event, deps.config.identity_header)
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

    upload = deps.store.presigned_post(document.s3_key, source_format=source_format)

    # source_format rides the audit event because the Document row holding it is
    # application state that dies with the project. Without it a legacy
    # registration is indistinguishable from a .docx one in the log, and no
    # metric filter can count the converter path or alarm on a rate spike.
    log_event(
        "document.registered",
        project_id=project_id,
        document_id=document.document_id,
        uploaded_by=identity,
        source_format=source_format,
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

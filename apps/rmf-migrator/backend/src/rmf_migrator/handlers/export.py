"""API handlers for Rev 5 export and the decision log.

POST .../documents/{document_id}/export            -> start async export job
GET  .../export-jobs/{job_id}                      -> poll export job
GET  .../documents/{document_id}/export/download   -> presigned GET for the .docx
GET  .../documents/{document_id}/decision-log.csv  -> per-control audit CSV
"""

from __future__ import annotations

import os
from typing import Any

from rmf_migrator.common.http import (
    HttpError,
    error_response,
    json_response,
    path_param,
)
from rmf_migrator.common.logging import log_event
from rmf_migrator.common.models import DocumentStatus, DraftStatus, ExportJob, JobStatus
from rmf_migrator.handlers.deps import Deps
from rmf_migrator.services.decision_log import build_rows, to_csv

_EXPORTABLE = {
    DocumentStatus.REVIEW_APPROVED,
    DocumentStatus.EXPORTED,
}


def _load(deps: Deps, event: dict[str, Any]):
    project_id = path_param(event, "project_id")
    document_id = path_param(event, "document_id")
    document = deps.repo.get_document(project_id, document_id)
    if document is None:
        raise HttpError(404, "document not found")
    return project_id, document_id, document


# ---- POST export -----------------------------------------------------------


def _enqueue_export(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    from rmf_migrator.handlers.worker import enqueue_export as enqueue_export_job

    project_id, document_id, document = _load(deps, event)
    if document.status == DocumentStatus.EXPORTING and document.active_job_id:
        existing = deps.repo.get_export_job(project_id, document.active_job_id)
        if existing is not None:
            return json_response(202, {"job": existing.model_dump()})
    if document.status not in _EXPORTABLE:
        raise HttpError(409, f"document is not ready to export (status: {document.status})")

    drafts = deps.repo.list_drafts(document_id)
    if not drafts or any(draft.status != DraftStatus.APPROVED for draft in drafts):
        raise HttpError(409, "every generated draft must be approved before export")

    previous_status = document.status
    job = ExportJob(
        project_id=project_id,
        document_id=document_id,
        previous_document_status=previous_status,
    )
    document.status = DocumentStatus.EXPORTING
    document.active_job_id = job.job_id
    document.failure_stage = None
    if not deps.repo.put_document_if_status(document, {previous_status}):
        raise HttpError(409, "document state changed; refresh and try again")
    try:
        enqueue_export_job(
            deps,
            project_id,
            document_id,
            previous_status=previous_status,
            job=job,
        )
    except Exception:
        document.status = previous_status
        document.active_job_id = None
        deps.repo.put_document(document)
        job.status = JobStatus.FAILED
        job.error_type = "EnqueueFailed"
        deps.repo.put_export_job(job)
        raise
    return json_response(202, {"job": job.model_dump()})


def enqueue_export(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _enqueue_export(event, Deps.build())
    except HttpError as err:
        return error_response(err)


# ---- GET export job --------------------------------------------------------


def _get_export_job(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id = path_param(event, "project_id")
    job_id = path_param(event, "job_id")
    job = deps.repo.get_export_job(project_id, job_id)
    if job is None:
        raise HttpError(404, "export job not found")
    return json_response(200, job.model_dump())


def get_export_job(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _get_export_job(event, Deps.build())
    except HttpError as err:
        return error_response(err)


# ---- GET download ----------------------------------------------------------


def _download_export(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    _, _, document = _load(deps, event)
    if not document.export_key:
        raise HttpError(409, "no export available yet; run export first")

    stem = os.path.splitext(document.filename)[0]
    download_name = f"{stem}-rev5.docx"
    target = deps.store.presigned_get_url(document.export_key, download_name=download_name)
    return json_response(200, target)


def download_export(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _download_export(event, Deps.build())
    except HttpError as err:
        return error_response(err)


# ---- GET decision log CSV --------------------------------------------------


def _decision_log(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id, document_id, _ = _load(deps, event)
    sections = deps.repo.list_sections(document_id)
    mappings = deps.repo.list_mappings(document_id)
    drafts = deps.repo.list_drafts(document_id)
    csv_text = to_csv(build_rows(sections, mappings, drafts))

    log_event(
        "decision_log.exported",
        project_id=project_id,
        document_id=document_id,
        row_count=len(sections),
    )
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "text/csv",
            "Content-Disposition": f'attachment; filename="decision-log-{document_id}.csv"',
        },
        "body": csv_text,
    }


def decision_log(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _decision_log(event, Deps.build())
    except HttpError as err:
        return error_response(err)

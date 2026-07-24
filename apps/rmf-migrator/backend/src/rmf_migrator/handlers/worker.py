"""SQS worker entrypoint — dispatches parse and mapping jobs.

One queue carries both job kinds; each message has a ``kind`` field
("parse" | "map"). A successful parse auto-enqueues the mapping job for the same
document, so the pipeline flows upload -> parse -> map -> (human review) without
extra client calls. The human checkpoint is at mapping *approval*, not here.

Message shapes:
  parse: {"kind": "parse", "job_id", "project_id", "document_id"}
  map:   {"kind": "map",   "job_id", "project_id", "document_id"}
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from rmf_migrator.common.logging import log_error, log_event
from rmf_migrator.common.models import (
    DocumentStatus,
    DraftJob,
    DraftStatus,
    ExportJob,
    JobStatus,
    MappingJob,
    MappingStatus,
)
from rmf_migrator.common.sections import hydrate_section_texts
from rmf_migrator.common.storage import build_export_key
from rmf_migrator.docx.export_docx import export_rev5_docx
from rmf_migrator.handlers.deps import Deps
from rmf_migrator.handlers.parse_document import run_parse_job
from rmf_migrator.services.drafting import draft_document
from rmf_migrator.services.mapping import map_document


def enqueue_mapping(deps: Deps, project_id: str, document_id: str) -> MappingJob:
    """Create a MappingJob and enqueue it for the worker."""
    job_id = f"mjob_{document_id}"
    existing = deps.repo.get_mapping_job(project_id, job_id)
    if existing is not None and existing.status in {JobStatus.RUNNING, JobStatus.SUCCEEDED}:
        return existing
    job = existing or MappingJob(job_id=job_id, project_id=project_id, document_id=document_id)
    if existing is None:
        deps.repo.put_mapping_job(job)
    deps.sqs.send_message(
        QueueUrl=deps.config.parse_queue_url,
        MessageBody=json.dumps(
            {
                "kind": "map",
                "job_id": job.job_id,
                "project_id": project_id,
                "document_id": document_id,
            }
        ),
    )
    log_event("mapping.enqueued", project_id=project_id, document_id=document_id, job_id=job.job_id)
    return job


def enqueue_drafting(deps: Deps, project_id: str, document_id: str) -> DraftJob:
    """Create a DraftJob and enqueue it for the worker (Rev 5 drafting)."""
    job_id = f"djob_{document_id}"
    existing = deps.repo.get_draft_job(project_id, job_id)
    if existing is not None and existing.status in {JobStatus.RUNNING, JobStatus.SUCCEEDED}:
        return existing
    job = existing or DraftJob(job_id=job_id, project_id=project_id, document_id=document_id)
    if existing is None:
        deps.repo.put_draft_job(job)
    deps.sqs.send_message(
        QueueUrl=deps.config.parse_queue_url,
        MessageBody=json.dumps(
            {
                "kind": "draft",
                "job_id": job.job_id,
                "project_id": project_id,
                "document_id": document_id,
            }
        ),
    )
    log_event(
        "drafting.enqueued", project_id=project_id, document_id=document_id, job_id=job.job_id
    )
    return job


def enqueue_export(
    deps: Deps,
    project_id: str,
    document_id: str,
    *,
    previous_status: DocumentStatus = DocumentStatus.REVIEW_APPROVED,
    job: ExportJob | None = None,
) -> ExportJob:
    """Create an ExportJob and enqueue it for the worker (Rev 5 DOCX export)."""
    job = job or ExportJob(
        project_id=project_id, document_id=document_id, previous_document_status=previous_status
    )
    deps.repo.put_export_job(job)
    deps.sqs.send_message(
        QueueUrl=deps.config.parse_queue_url,
        MessageBody=json.dumps(
            {
                "kind": "export",
                "job_id": job.job_id,
                "project_id": project_id,
                "document_id": document_id,
            }
        ),
    )
    log_event("export.enqueued", project_id=project_id, document_id=document_id, job_id=job.job_id)
    return job


def run_mapping_job(project_id: str, document_id: str, job_id: str, deps: Deps) -> None:
    existing_job = deps.repo.get_mapping_job(project_id, job_id)
    document = deps.repo.get_document(project_id, document_id)
    if existing_job is None or document is None:
        log_event(
            "mapping.job_missing",
            project_id=project_id,
            document_id=document_id,
            job_id=job_id,
            job_found=existing_job is not None,
            document_found=document is not None,
        )
        return

    if existing_job.document_id != document_id or existing_job.status == JobStatus.SUCCEEDED:
        return
    if document.status in {
        DocumentStatus.MAPPED,
        DocumentStatus.MAPPING_APPROVED,
        DocumentStatus.DRAFTING,
        DocumentStatus.DRAFTED,
        DocumentStatus.REVIEW_APPROVED,
        DocumentStatus.EXPORTING,
        DocumentStatus.EXPORTED,
    }:
        mappings = deps.repo.list_mappings(document_id)
        if mappings:
            existing_job.status = JobStatus.SUCCEEDED
            existing_job.section_count = len(mappings)
            existing_job.error_type = None
            existing_job.updated_at = datetime.now(UTC)
            deps.repo.put_mapping_job(existing_job)
        return
    job = deps.repo.claim_mapping_job(project_id, job_id)
    if job is None:
        return
    if document.status not in {DocumentStatus.PARSED, DocumentStatus.MAPPING} and not (
        document.status == DocumentStatus.FAILED and document.failure_stage == "mapping"
    ):
        job.status = JobStatus.FAILED
        job.error_type = "InvalidDocumentState"
        job.updated_at = datetime.now(UTC)
        deps.repo.put_mapping_job(job)
        return

    document.status = DocumentStatus.MAPPING
    document.failure_stage = None
    deps.repo.put_document(document)

    try:
        sections = hydrate_section_texts(deps.repo.list_sections(document_id), deps.store)
        mappings = map_document(sections, deps.bedrock)
        deps.repo.put_mappings(mappings)

        document.status = DocumentStatus.MAPPED
        document.failure_stage = None
        deps.repo.put_document(document)

        job.status = JobStatus.SUCCEEDED
        job.section_count = len(mappings)
        job.error_type = None
        job.updated_at = datetime.now(UTC)
        deps.repo.put_mapping_job(job)

        low_confidence = sum(1 for m in mappings if m.confidence < 0.5)
        log_event(
            "document.mapped",
            project_id=project_id,
            document_id=document_id,
            job_id=job_id,
            section_count=len(mappings),
            low_confidence_count=low_confidence,
        )
    except Exception as exc:  # noqa: BLE001 — record failure, don't crash silently
        document.status = DocumentStatus.FAILED
        document.failure_stage = "mapping"
        deps.repo.put_document(document)
        job.status = JobStatus.FAILED
        job.error_type = type(exc).__name__
        job.updated_at = datetime.now(UTC)
        deps.repo.put_mapping_job(job)
        log_error("document.mapping_failed", exc, project_id=project_id, document_id=document_id)
        raise


def run_draft_job(project_id: str, document_id: str, job_id: str, deps: Deps) -> None:
    existing_job = deps.repo.get_draft_job(project_id, job_id)
    document = deps.repo.get_document(project_id, document_id)
    if existing_job is None or document is None:
        log_event(
            "drafting.job_missing",
            project_id=project_id,
            document_id=document_id,
            job_id=job_id,
            job_found=existing_job is not None,
            document_found=document is not None,
        )
        return

    if existing_job.document_id != document_id or existing_job.status == JobStatus.SUCCEEDED:
        return
    if document.status in {
        DocumentStatus.DRAFTED,
        DocumentStatus.REVIEW_APPROVED,
        DocumentStatus.EXPORTING,
        DocumentStatus.EXPORTED,
    }:
        drafts = deps.repo.list_drafts(document_id)
        if drafts:
            existing_job.status = JobStatus.SUCCEEDED
            existing_job.section_count = len(drafts)
            existing_job.error_type = None
            existing_job.updated_at = datetime.now(UTC)
            deps.repo.put_draft_job(existing_job)
        return
    job = deps.repo.claim_draft_job(project_id, job_id)
    if job is None:
        return
    if document.status not in {DocumentStatus.MAPPING_APPROVED, DocumentStatus.DRAFTING} and not (
        document.status == DocumentStatus.FAILED and document.failure_stage == "drafting"
    ):
        job.status = JobStatus.FAILED
        job.error_type = "InvalidDocumentState"
        job.updated_at = datetime.now(UTC)
        deps.repo.put_draft_job(job)
        return

    document.status = DocumentStatus.DRAFTING
    document.failure_stage = None
    deps.repo.put_document(document)

    try:
        sections = hydrate_section_texts(deps.repo.list_sections(document_id), deps.store)
        # Only draft from human-approved mappings.
        mappings = [
            m for m in deps.repo.list_mappings(document_id) if m.status == MappingStatus.APPROVED
        ]
        drafts = draft_document(sections, mappings, deps.bedrock)
        deps.repo.put_drafts(drafts)

        document.status = DocumentStatus.DRAFTED
        document.failure_stage = None
        deps.repo.put_document(document)

        job.status = JobStatus.SUCCEEDED
        job.section_count = len(drafts)
        job.error_type = None
        job.updated_at = datetime.now(UTC)
        deps.repo.put_draft_job(job)

        log_event(
            "document.drafted",
            project_id=project_id,
            document_id=document_id,
            job_id=job_id,
            draft_count=len(drafts),
        )
    except Exception as exc:  # noqa: BLE001
        document.status = DocumentStatus.FAILED
        document.failure_stage = "drafting"
        deps.repo.put_document(document)
        job.status = JobStatus.FAILED
        job.error_type = type(exc).__name__
        job.updated_at = datetime.now(UTC)
        deps.repo.put_draft_job(job)
        log_error("document.drafting_failed", exc, project_id=project_id, document_id=document_id)
        raise


def run_export_job(project_id: str, document_id: str, job_id: str, deps: Deps) -> None:
    existing_job = deps.repo.get_export_job(project_id, job_id)
    document = deps.repo.get_document(project_id, document_id)
    if existing_job is None or document is None:
        log_event(
            "export.job_missing",
            project_id=project_id,
            document_id=document_id,
            job_id=job_id,
            job_found=existing_job is not None,
            document_found=document is not None,
        )
        return

    if existing_job.document_id != document_id or existing_job.status == JobStatus.SUCCEEDED:
        return
    if document.status == DocumentStatus.EXPORTED and document.export_key:
        existing_job.status = JobStatus.SUCCEEDED
        existing_job.error_type = None
        existing_job.updated_at = datetime.now(UTC)
        deps.repo.put_export_job(existing_job)
        return
    job = deps.repo.claim_export_job(project_id, job_id)
    if job is None:
        return
    if document.status != DocumentStatus.EXPORTING:
        job.status = JobStatus.FAILED
        job.error_type = "InvalidDocumentState"
        job.updated_at = datetime.now(UTC)
        deps.repo.put_export_job(job)
        return

    prior_status = job.previous_document_status

    try:
        original = deps.store.get_bytes(document.s3_key)
        drafts = deps.repo.list_drafts(document_id)
        if not drafts or any(d.status != DraftStatus.APPROVED for d in drafts):
            raise ValueError("every generated draft must be approved before export")
        drafts_by_order = {
            d.order: text
            for d in drafts
            if d.status == DraftStatus.APPROVED and (text := d.effective_text()).strip()
        }
        new_docx = export_rev5_docx(original, drafts_by_order)

        export_key = build_export_key(project_id, document_id)
        deps.store.put_bytes(export_key, new_docx)

        document.status = DocumentStatus.EXPORTED
        document.export_key = export_key
        document.failure_stage = None
        document.active_job_id = None
        deps.repo.put_document(document)

        job.status = JobStatus.SUCCEEDED
        job.error_type = None
        job.updated_at = datetime.now(UTC)
        deps.repo.put_export_job(job)

        log_event(
            "document.exported",
            project_id=project_id,
            document_id=document_id,
            job_id=job_id,
            section_count=len(drafts_by_order),
        )
    except Exception as exc:  # noqa: BLE001
        # Don't leave the document stuck in EXPORTING; restore prior state.
        document.status = prior_status
        document.failure_stage = "export"
        document.active_job_id = None
        deps.repo.put_document(document)
        job.status = JobStatus.FAILED
        job.error_type = type(exc).__name__
        job.updated_at = datetime.now(UTC)
        deps.repo.put_export_job(job)
        log_error("document.export_failed", exc, project_id=project_id, document_id=document_id)
        raise


def process_event(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    """Dispatch each SQS record; returns partial-batch-failure identifiers.

    Separated from ``handler`` so tests can drive it with injected deps.
    """
    failures: list[dict[str, str]] = []

    for record in event.get("Records", []):
        try:
            body = json.loads(record["body"])
            kind = body.get("kind", "parse")
            if kind == "parse":
                parsed = run_parse_job(
                    project_id=body["project_id"],
                    document_id=body["document_id"],
                    job_id=body["job_id"],
                    deps=deps,
                )
                # Auto-chain into mapping now that sections exist.
                if parsed:
                    enqueue_mapping(deps, body["project_id"], body["document_id"])
            elif kind == "map":
                run_mapping_job(
                    project_id=body["project_id"],
                    document_id=body["document_id"],
                    job_id=body["job_id"],
                    deps=deps,
                )
            elif kind == "draft":
                run_draft_job(
                    project_id=body["project_id"],
                    document_id=body["document_id"],
                    job_id=body["job_id"],
                    deps=deps,
                )
            elif kind == "export":
                run_export_job(
                    project_id=body["project_id"],
                    document_id=body["document_id"],
                    job_id=body["job_id"],
                    deps=deps,
                )
            else:
                log_event("worker.unknown_kind", kind=str(kind)[:32])
                raise ValueError("unknown worker message kind")
        except Exception:  # noqa: BLE001
            failures.append({"itemIdentifier": record.get("messageId", "")})

    return {"batchItemFailures": failures}


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """SQS batch entrypoint."""
    return process_event(event, Deps.build())

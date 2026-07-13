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
from typing import Any

from rmf_migrator.common.logging import log_error, log_event
from rmf_migrator.common.models import (
    DocumentStatus,
    DraftJob,
    ExportJob,
    JobStatus,
    MappingJob,
    MappingStatus,
)
from rmf_migrator.common.storage import build_export_key
from rmf_migrator.docx.export_docx import export_rev5_docx
from rmf_migrator.handlers.deps import Deps
from rmf_migrator.handlers.parse_document import run_parse_job
from rmf_migrator.services.drafting import draft_document
from rmf_migrator.services.mapping import map_document


def enqueue_mapping(deps: Deps, project_id: str, document_id: str) -> MappingJob:
    """Create a MappingJob and enqueue it for the worker."""
    job = MappingJob(project_id=project_id, document_id=document_id)
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
    job = DraftJob(project_id=project_id, document_id=document_id)
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


def enqueue_export(deps: Deps, project_id: str, document_id: str) -> ExportJob:
    """Create an ExportJob and enqueue it for the worker (Rev 5 DOCX export)."""
    job = ExportJob(project_id=project_id, document_id=document_id)
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
    job = deps.repo.get_mapping_job(project_id, job_id)
    document = deps.repo.get_document(project_id, document_id)
    if job is None or document is None:
        log_event(
            "mapping.job_missing",
            project_id=project_id,
            document_id=document_id,
            job_id=job_id,
            job_found=job is not None,
            document_found=document is not None,
        )
        return

    job.status = JobStatus.RUNNING
    deps.repo.put_mapping_job(job)
    document.status = DocumentStatus.MAPPING
    deps.repo.put_document(document)

    try:
        sections = deps.repo.list_sections(document_id)
        mappings = map_document(sections, deps.bedrock)
        deps.repo.put_mappings(mappings)

        document.status = DocumentStatus.MAPPED
        deps.repo.put_document(document)

        job.status = JobStatus.SUCCEEDED
        job.section_count = len(mappings)
        job.error_type = None
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
        deps.repo.put_document(document)
        job.status = JobStatus.FAILED
        job.error_type = type(exc).__name__
        deps.repo.put_mapping_job(job)
        log_error("document.mapping_failed", exc, project_id=project_id, document_id=document_id)
        raise


def run_draft_job(project_id: str, document_id: str, job_id: str, deps: Deps) -> None:
    job = deps.repo.get_draft_job(project_id, job_id)
    document = deps.repo.get_document(project_id, document_id)
    if job is None or document is None:
        log_event(
            "drafting.job_missing",
            project_id=project_id,
            document_id=document_id,
            job_id=job_id,
            job_found=job is not None,
            document_found=document is not None,
        )
        return

    job.status = JobStatus.RUNNING
    deps.repo.put_draft_job(job)
    document.status = DocumentStatus.DRAFTING
    deps.repo.put_document(document)

    try:
        sections = deps.repo.list_sections(document_id)
        # Only draft from human-approved mappings.
        mappings = [
            m for m in deps.repo.list_mappings(document_id) if m.status == MappingStatus.APPROVED
        ]
        drafts = draft_document(sections, mappings, deps.bedrock)
        deps.repo.put_drafts(drafts)

        document.status = DocumentStatus.DRAFTED
        deps.repo.put_document(document)

        job.status = JobStatus.SUCCEEDED
        job.section_count = len(drafts)
        job.error_type = None
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
        deps.repo.put_document(document)
        job.status = JobStatus.FAILED
        job.error_type = type(exc).__name__
        deps.repo.put_draft_job(job)
        log_error("document.drafting_failed", exc, project_id=project_id, document_id=document_id)
        raise


def run_export_job(project_id: str, document_id: str, job_id: str, deps: Deps) -> None:
    job = deps.repo.get_export_job(project_id, job_id)
    document = deps.repo.get_document(project_id, document_id)
    if job is None or document is None:
        log_event(
            "export.job_missing",
            project_id=project_id,
            document_id=document_id,
            job_id=job_id,
            job_found=job is not None,
            document_found=document is not None,
        )
        return

    prior_status = document.status
    job.status = JobStatus.RUNNING
    deps.repo.put_export_job(job)
    document.status = DocumentStatus.EXPORTING
    deps.repo.put_document(document)

    try:
        original = deps.store.get_bytes(document.s3_key)
        drafts = deps.repo.list_drafts(document_id)
        # Use each section's authoritative (edited-or-proposed) Rev 5 text.
        drafts_by_order = {d.order: d.effective_text() for d in drafts}
        new_docx = export_rev5_docx(original, drafts_by_order)

        export_key = build_export_key(project_id, document_id)
        deps.store.put_bytes(export_key, new_docx)

        document.status = DocumentStatus.EXPORTED
        document.export_key = export_key
        deps.repo.put_document(document)

        job.status = JobStatus.SUCCEEDED
        job.error_type = None
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
        deps.repo.put_document(document)
        job.status = JobStatus.FAILED
        job.error_type = type(exc).__name__
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
                run_parse_job(
                    project_id=body["project_id"],
                    document_id=body["document_id"],
                    job_id=body["job_id"],
                    deps=deps,
                )
                # Auto-chain into mapping now that sections exist.
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
        except Exception:  # noqa: BLE001
            failures.append({"itemIdentifier": record.get("messageId", "")})

    return {"batchItemFailures": failures}


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """SQS batch entrypoint."""
    return process_event(event, Deps.build())

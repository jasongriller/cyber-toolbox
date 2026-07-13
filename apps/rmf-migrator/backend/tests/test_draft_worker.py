"""Draft worker + mapping-approval auto-chain tests (moto + fake Bedrock)."""

from __future__ import annotations

import json

from rmf_migrator.common.models import (
    ControlMapping,
    Document,
    DocumentStatus,
    DraftJob,
    JobStatus,
    MappingStatus,
    Project,
    Section,
)
from rmf_migrator.handlers.review import _approve_mappings
from rmf_migrator.handlers.worker import process_event, run_draft_job
from tests.conftest import FakeBedrock, sqs_event

_DRAFT_RESULT = {
    "draft_text": "Updated Rev 5 policy language.",
    "suggestions": ["Add a review cadence."],
}


def _event(path=None, headers=None):
    return {"body": None, "pathParameters": path or {}, "headers": headers or {}}


def _seed_approved_mapping_document(deps) -> tuple[str, str]:
    project = Project(name="Sys")
    deps.repo.put_project(project)
    pid = project.project_id
    document = Document(project_id=pid, filename="a.docx", s3_key="k")
    document.status = DocumentStatus.MAPPING_APPROVED
    deps.repo.put_document(document)
    did = document.document_id

    sections = [
        Section(document_id=did, project_id=pid, order=0, level=1, heading="AC Policy", text="x"),
        Section(document_id=did, project_id=pid, order=1, level=2, heading="AC-2", text="y"),
    ]
    deps.repo.put_sections(sections)
    mappings = [
        ControlMapping(
            project_id=pid,
            document_id=did,
            section_id=sections[0].section_id,
            order=0,
            final_control_ids=["AC-1"],
            status=MappingStatus.APPROVED,
        ),
        ControlMapping(
            project_id=pid,
            document_id=did,
            section_id=sections[1].section_id,
            order=1,
            final_control_ids=["AC-2"],
            status=MappingStatus.APPROVED,
        ),
    ]
    deps.repo.put_mappings(mappings)
    return pid, did


def test_run_draft_job_persists_drafts_and_status(deps):
    deps.bedrock = FakeBedrock(_DRAFT_RESULT)
    pid, did = _seed_approved_mapping_document(deps)
    job = DraftJob(project_id=pid, document_id=did)
    deps.repo.put_draft_job(job)

    run_draft_job(pid, did, job.job_id, deps)

    assert deps.repo.get_document(pid, did).status == DocumentStatus.DRAFTED
    done = deps.repo.get_draft_job(pid, job.job_id)
    assert done.status == JobStatus.SUCCEEDED
    assert done.section_count == 2

    drafts = deps.repo.list_drafts(did)
    assert len(drafts) == 2
    assert drafts[0].rev5_control_ids == ["AC-1"]
    assert drafts[0].draft_text == "Updated Rev 5 policy language."
    assert [d.order for d in drafts] == [0, 1]


def test_run_draft_job_ignores_unapproved_mappings(deps):
    deps.bedrock = FakeBedrock(_DRAFT_RESULT)
    pid, did = _seed_approved_mapping_document(deps)
    # Add a third section whose mapping is only PROPOSED (not approved).
    extra = Section(document_id=did, project_id=pid, order=2, level=2, heading="X", text="z")
    deps.repo.put_sections([extra])
    deps.repo.put_mapping(
        ControlMapping(
            project_id=pid,
            document_id=did,
            section_id=extra.section_id,
            order=2,
            proposed_control_ids=["AC-3"],
            status=MappingStatus.PROPOSED,
        )
    )
    job = DraftJob(project_id=pid, document_id=did)
    deps.repo.put_draft_job(job)

    run_draft_job(pid, did, job.job_id, deps)
    drafts = deps.repo.list_drafts(did)
    # Only the two approved sections were drafted.
    assert {d.section_id for d in drafts} == {
        m.section_id for m in deps.repo.list_mappings(did) if m.status == MappingStatus.APPROVED
    }
    assert len(drafts) == 2


def test_approve_mappings_auto_enqueues_draft_job(deps):
    # Seed a MAPPED document with proposals, then approve -> should enqueue draft.
    project = Project(name="Sys")
    deps.repo.put_project(project)
    pid = project.project_id
    document = Document(project_id=pid, filename="a.docx", s3_key="k")
    document.status = DocumentStatus.MAPPED
    deps.repo.put_document(document)
    did = document.document_id
    section = Section(document_id=did, project_id=pid, order=0, level=1, heading="H", text="t")
    deps.repo.put_sections([section])
    deps.repo.put_mapping(
        ControlMapping(
            project_id=pid,
            document_id=did,
            section_id=section.section_id,
            order=0,
            proposed_control_ids=["AC-1"],
        )
    )

    resp = _approve_mappings(_event(path={"project_id": pid, "document_id": did}), deps)
    body = json.loads(resp["body"])
    assert "draft_job_id" in body
    assert deps.repo.get_draft_job(pid, body["draft_job_id"]) is not None

    msgs = deps.sqs.receive_message(
        QueueUrl=deps.config.parse_queue_url, MaxNumberOfMessages=10
    ).get("Messages", [])
    kinds = [json.loads(m["Body"])["kind"] for m in msgs]
    assert "draft" in kinds


def test_draft_message_dispatch_runs_drafting(deps):
    deps.bedrock = FakeBedrock(_DRAFT_RESULT)
    pid, did = _seed_approved_mapping_document(deps)
    job = DraftJob(project_id=pid, document_id=did)
    deps.repo.put_draft_job(job)

    process_event(
        sqs_event({"kind": "draft", "job_id": job.job_id, "project_id": pid, "document_id": did}),
        deps,
    )
    assert deps.repo.get_document(pid, did).status == DocumentStatus.DRAFTED

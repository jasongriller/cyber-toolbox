"""Tests for the export / download / decision-log API handlers."""

from __future__ import annotations

import json

import pytest

from rmf_migrator.common.http import HttpError
from rmf_migrator.common.models import (
    ControlMapping,
    Document,
    DocumentStatus,
    Draft,
    DraftStatus,
    Project,
    Section,
)
from rmf_migrator.handlers.export import (
    _decision_log,
    _download_export,
    _enqueue_export,
    _get_export_job,
)


def _event(path=None):
    return {"body": None, "pathParameters": path or {}, "headers": {}}


def _seed(deps, *, status=DocumentStatus.REVIEW_APPROVED, export_key=None):
    project = Project(name="Sys")
    deps.repo.put_project(project)
    pid = project.project_id
    document = Document(project_id=pid, filename="ac.docx", s3_key="k")
    document.status = status
    document.export_key = export_key
    deps.repo.put_document(document)
    did = document.document_id
    section = Section(document_id=did, project_id=pid, order=0, level=1, heading="AC Policy")
    deps.repo.put_sections([section])
    deps.repo.put_mapping(
        ControlMapping(
            project_id=pid,
            document_id=did,
            section_id=section.section_id,
            order=0,
            final_control_ids=["AC-1"],
        )
    )
    deps.repo.put_drafts(
        [
            Draft(
                project_id=pid,
                document_id=did,
                section_id=section.section_id,
                order=0,
                rev5_control_ids=["AC-1"],
                draft_text="text",
                status=DraftStatus.APPROVED,
            )
        ]
    )
    return pid, did


def test_enqueue_export_when_review_approved(deps):
    pid, did = _seed(deps, status=DocumentStatus.REVIEW_APPROVED)
    resp = _enqueue_export(_event(path={"project_id": pid, "document_id": did}), deps)
    assert resp["statusCode"] == 202
    job = json.loads(resp["body"])["job"]
    assert deps.repo.get_export_job(pid, job["job_id"]) is not None

    msgs = deps.sqs.receive_message(
        QueueUrl=deps.config.parse_queue_url, MaxNumberOfMessages=10
    ).get("Messages", [])
    assert any(json.loads(m["Body"])["kind"] == "export" for m in msgs)


def test_enqueue_export_rejects_premature_document(deps):
    pid, did = _seed(deps, status=DocumentStatus.MAPPED)
    with pytest.raises(HttpError) as exc:
        _enqueue_export(_event(path={"project_id": pid, "document_id": did}), deps)
    assert exc.value.status == 409


def test_download_export_409_without_export(deps):
    pid, did = _seed(deps, status=DocumentStatus.DRAFTED, export_key=None)
    with pytest.raises(HttpError) as exc:
        _download_export(_event(path={"project_id": pid, "document_id": did}), deps)
    assert exc.value.status == 409


def test_download_export_returns_presigned_url(deps):
    pid, did = _seed(
        deps, status=DocumentStatus.EXPORTED, export_key="projects/x/exports/doc-rev5.docx"
    )
    resp = _download_export(_event(path={"project_id": pid, "document_id": did}), deps)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "url" in body and body["expires_in"] > 0


def test_decision_log_returns_csv(deps):
    pid, did = _seed(deps)
    resp = _decision_log(_event(path={"project_id": pid, "document_id": did}), deps)
    assert resp["statusCode"] == 200
    assert resp["headers"]["Content-Type"] == "text/csv"
    assert "attachment" in resp["headers"]["Content-Disposition"]
    # Header row + one data row.
    lines = resp["body"].strip().splitlines()
    assert lines[0].startswith("order,heading,rev4_controls")
    assert "AC Policy" in resp["body"]
    assert "AC-1" in resp["body"]


def test_get_export_job_404(deps):
    project = Project(name="S")
    deps.repo.put_project(project)
    with pytest.raises(HttpError) as exc:
        _get_export_job(
            _event(path={"project_id": project.project_id, "job_id": "xjob_missing"}), deps
        )
    assert exc.value.status == 404

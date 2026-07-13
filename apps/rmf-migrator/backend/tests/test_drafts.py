"""Tests for the draft review API handlers."""

from __future__ import annotations

import json

import pytest

from rmf_migrator.common.http import HttpError
from rmf_migrator.common.models import (
    Document,
    DocumentStatus,
    Draft,
    DraftStatus,
    Project,
    Section,
)
from rmf_migrator.handlers.drafts import _approve_draft, _get_drafts, _update_draft


def _event(body=None, path=None, headers=None):
    return {
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "headers": headers or {},
    }


def _seed_drafted_document(deps):
    project = Project(name="Sys")
    deps.repo.put_project(project)
    pid = project.project_id
    document = Document(project_id=pid, filename="a.docx", s3_key="k")
    document.status = DocumentStatus.DRAFTED
    deps.repo.put_document(document)
    did = document.document_id
    section = Section(document_id=did, project_id=pid, order=0, level=1, heading="AC", text="x")
    deps.repo.put_sections([section])
    draft = Draft(
        project_id=pid,
        document_id=did,
        section_id=section.section_id,
        order=0,
        rev4_control_ids=["AC-1"],
        rev5_control_ids=["AC-1"],
        draft_text="Proposed Rev 5 text.",
        suggestions=["Add cadence."],
    )
    deps.repo.put_drafts([draft])
    return pid, did, section.section_id


def test_get_drafts_returns_drafts_and_status(deps):
    pid, did, _ = _seed_drafted_document(deps)
    resp = _get_drafts(_event(path={"project_id": pid, "document_id": did}), deps)
    body = json.loads(resp["body"])
    assert body["document_status"] == DocumentStatus.DRAFTED.value
    assert len(body["drafts"]) == 1
    assert body["drafts"][0]["draft_text"] == "Proposed Rev 5 text."


def test_update_draft_sets_edited_text_and_status(deps):
    pid, did, sid = _seed_drafted_document(deps)
    resp = _update_draft(
        _event(
            body={"text": "Human-refined Rev 5 policy text."},
            path={"project_id": pid, "document_id": did, "section_id": sid},
            headers={"X-Remote-User": "jdoe"},
        ),
        deps,
    )
    draft = json.loads(resp["body"])
    assert draft["edited_text"] == "Human-refined Rev 5 policy text."
    assert draft["status"] == DraftStatus.EDITED.value
    assert draft["reviewed_by"] == "jdoe"


def test_update_draft_requires_text(deps):
    pid, did, sid = _seed_drafted_document(deps)
    with pytest.raises(HttpError) as exc:
        _update_draft(
            _event(body={}, path={"project_id": pid, "document_id": did, "section_id": sid}),
            deps,
        )
    assert exc.value.status == 400


def test_approve_draft_freezes_effective_text(deps):
    pid, did, sid = _seed_drafted_document(deps)
    # Edit first, then approve -> approved text is the edit.
    _update_draft(
        _event(
            body={"text": "Final text."},
            path={"project_id": pid, "document_id": did, "section_id": sid},
        ),
        deps,
    )
    resp = _approve_draft(
        _event(path={"project_id": pid, "document_id": did, "section_id": sid}), deps
    )
    draft = json.loads(resp["body"])
    assert draft["status"] == DraftStatus.APPROVED.value
    assert draft["edited_text"] == "Final text."


def test_approve_draft_without_edit_freezes_proposal(deps):
    pid, did, sid = _seed_drafted_document(deps)
    resp = _approve_draft(
        _event(path={"project_id": pid, "document_id": did, "section_id": sid}), deps
    )
    draft = json.loads(resp["body"])
    assert draft["status"] == DraftStatus.APPROVED.value
    assert draft["edited_text"] == "Proposed Rev 5 text."


def test_update_draft_404_missing(deps):
    pid, did, _ = _seed_drafted_document(deps)
    with pytest.raises(HttpError) as exc:
        _update_draft(
            _event(
                body={"text": "x"},
                path={"project_id": pid, "document_id": did, "section_id": "sec_missing"},
            ),
            deps,
        )
    assert exc.value.status == 404

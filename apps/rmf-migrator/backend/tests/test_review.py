"""Tests for the mapping-review API handlers (the human checkpoint)."""

from __future__ import annotations

import json

import pytest

from rmf_migrator.common.http import HttpError
from rmf_migrator.common.models import (
    ControlMapping,
    Document,
    DocumentStatus,
    MappingStatus,
    Project,
    Section,
)
from rmf_migrator.handlers.review import (
    _approve_mappings,
    _get_document,
    _get_mappings,
    _list_sections,
    _update_mapping,
)


def _event(body=None, path=None, headers=None):
    return {
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "headers": headers or {},
    }


def _seed_mapped_document(deps, *, status=DocumentStatus.MAPPED):
    project = Project(name="Sys")
    deps.repo.put_project(project)
    pid = project.project_id
    document = Document(project_id=pid, filename="a.docx", s3_key="k")
    document.status = status
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
            proposed_control_ids=["AC-1"],
            confidence=0.7,
        ),
        ControlMapping(
            project_id=pid,
            document_id=did,
            section_id=sections[1].section_id,
            order=1,
            proposed_control_ids=["AC-2"],
            confidence=0.9,
        ),
    ]
    deps.repo.put_mappings(mappings)
    return pid, did, sections


def test_get_document_returns_status(deps):
    pid, did, _ = _seed_mapped_document(deps)
    resp = _get_document(_event(path={"project_id": pid, "document_id": did}), deps)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["status"] == DocumentStatus.MAPPED.value


def test_list_sections_returns_in_order(deps):
    pid, did, _ = _seed_mapped_document(deps)
    resp = _list_sections(_event(path={"project_id": pid, "document_id": did}), deps)
    sections = json.loads(resp["body"])["sections"]
    assert [s["order"] for s in sections] == [0, 1]
    assert sections[0]["heading"] == "AC Policy"


def test_get_mappings_returns_proposals_and_status(deps):
    pid, did, _ = _seed_mapped_document(deps)
    resp = _get_mappings(_event(path={"project_id": pid, "document_id": did}), deps)
    body = json.loads(resp["body"])
    assert body["document_status"] == DocumentStatus.MAPPED.value
    assert len(body["mappings"]) == 2
    assert body["mappings"][0]["proposed_control_ids"] == ["AC-1"]


def test_update_mapping_sets_final_and_edited_status(deps):
    pid, did, sections = _seed_mapped_document(deps)
    sid = sections[1].section_id
    resp = _update_mapping(
        _event(
            body={"control_ids": ["AC-2", "AC-2(1)"]},
            path={"project_id": pid, "document_id": did, "section_id": sid},
            headers={"X-Remote-User": "jdoe"},
        ),
        deps,
    )
    mapping = json.loads(resp["body"])
    assert mapping["final_control_ids"] == ["AC-2", "AC-2(1)"]
    assert mapping["status"] == MappingStatus.EDITED.value
    assert mapping["reviewed_by"] == "jdoe"


def test_update_mapping_rejects_unknown_control(deps):
    pid, did, sections = _seed_mapped_document(deps)
    sid = sections[0].section_id
    with pytest.raises(HttpError) as exc:
        _update_mapping(
            _event(
                body={"control_ids": ["AC-1", "ZZ-99"]},
                path={"project_id": pid, "document_id": did, "section_id": sid},
            ),
            deps,
        )
    assert exc.value.status == 400
    assert "ZZ-99" in exc.value.message


def test_update_mapping_normalizes_and_dedupes(deps):
    pid, did, sections = _seed_mapped_document(deps)
    sid = sections[0].section_id
    resp = _update_mapping(
        _event(
            body={"control_ids": ["ac-1", "AC-1", "au-2"]},
            path={"project_id": pid, "document_id": did, "section_id": sid},
        ),
        deps,
    )
    assert json.loads(resp["body"])["final_control_ids"] == ["AC-1", "AU-2"]


def test_approve_freezes_mappings_and_advances_status(deps):
    pid, did, sections = _seed_mapped_document(deps)
    # Edit one first; the other stays a bare proposal.
    _update_mapping(
        _event(
            body={"control_ids": ["AC-2(1)"]},
            path={"project_id": pid, "document_id": did, "section_id": sections[1].section_id},
        ),
        deps,
    )
    resp = _approve_mappings(
        _event(path={"project_id": pid, "document_id": did}, headers={"X-Remote-User": "boss"}),
        deps,
    )
    body = json.loads(resp["body"])
    assert body["document_status"] == DocumentStatus.MAPPING_APPROVED.value
    assert body["approved_count"] == 2

    assert deps.repo.get_document(pid, did).status == DocumentStatus.MAPPING_APPROVED
    mappings = deps.repo.list_mappings(did)
    assert all(m.status == MappingStatus.APPROVED for m in mappings)
    # Proposal with no human edit is frozen to its proposed set.
    assert mappings[0].final_control_ids == ["AC-1"]
    # Edited one keeps the human's choice.
    assert mappings[1].final_control_ids == ["AC-2(1)"]


def test_approve_rejects_document_not_yet_mapped(deps):
    pid, did, _ = _seed_mapped_document(deps, status=DocumentStatus.PARSING)
    with pytest.raises(HttpError) as exc:
        _approve_mappings(_event(path={"project_id": pid, "document_id": did}), deps)
    assert exc.value.status == 409


def test_get_document_404(deps):
    project = Project(name="S")
    deps.repo.put_project(project)
    with pytest.raises(HttpError) as exc:
        _get_document(_event(path={"project_id": project.project_id, "document_id": "doc_x"}), deps)
    assert exc.value.status == 404

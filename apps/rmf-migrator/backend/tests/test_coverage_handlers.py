"""Tests for the coverage + conversion-matrix API handlers (moto-backed)."""

from __future__ import annotations

import json

import pytest

from rmf_migrator.common.http import HttpError
from rmf_migrator.common.models import (
    Baseline,
    ControlMapping,
    Document,
    Draft,
    DraftStatus,
    MappingStatus,
    Project,
    Section,
)
from rmf_migrator.handlers.coverage import _conversion_matrix, _coverage, _oscal


def _event(path=None, query=None):
    return {"pathParameters": path or {}, "queryStringParameters": query, "headers": {}}


def _seed_project_with_two_docs(deps, baseline=Baseline.FIPS_199_LOW):
    project = Project(name="Sys", baseline=baseline)
    deps.repo.put_project(project)
    pid = project.project_id

    for fname, controls in [("ac.docx", ["AC-1"]), ("au.docx", ["AU-2"])]:
        document = Document(project_id=pid, filename=fname, s3_key="k")
        deps.repo.put_document(document)
        did = document.document_id
        section = Section(
            document_id=did, project_id=pid, order=0, level=1, heading=f"{controls[0]} section"
        )
        deps.repo.put_sections([section])
        deps.repo.put_mapping(
            ControlMapping(
                project_id=pid,
                document_id=did,
                section_id=section.section_id,
                order=0,
                final_control_ids=controls,
                status=MappingStatus.APPROVED,
            )
        )
        deps.repo.put_drafts(
            [
                Draft(
                    project_id=pid,
                    document_id=did,
                    section_id=section.section_id,
                    order=0,
                    rev4_control_ids=controls,
                    rev5_control_ids=controls,
                    status=DraftStatus.APPROVED,
                )
            ]
        )
    return pid


def test_coverage_uses_project_baseline(deps):
    pid = _seed_project_with_two_docs(deps, baseline=Baseline.FIPS_199_LOW)
    resp = _coverage(_event(path={"project_id": pid}), deps)
    body = json.loads(resp["body"])
    assert body["baseline"] == "low"
    assert "AC-1" in body["covered_controls"]
    assert "AU-2" in body["covered_controls"]
    assert body["baseline_total"] > 0
    # SR family not covered -> present in new-in-rev5 gaps.
    assert any(c.startswith("SR-") for c in body["new_in_rev5_gaps"])


def test_coverage_baseline_override_query(deps):
    pid = _seed_project_with_two_docs(deps, baseline=Baseline.GENERIC_800_53)
    resp = _coverage(_event(path={"project_id": pid}, query={"baseline": "high"}), deps)
    assert json.loads(resp["body"])["baseline"] == "high"


def test_coverage_bad_baseline_override_400(deps):
    pid = _seed_project_with_two_docs(deps)
    with pytest.raises(HttpError) as exc:
        _coverage(_event(path={"project_id": pid}, query={"baseline": "nope"}), deps)
    assert exc.value.status == 400


def test_coverage_404_missing_project(deps):
    with pytest.raises(HttpError) as exc:
        _coverage(_event(path={"project_id": "proj_missing"}), deps)
    assert exc.value.status == 404


def test_conversion_matrix_csv_spans_documents(deps):
    pid = _seed_project_with_two_docs(deps)
    resp = _conversion_matrix(_event(path={"project_id": pid}), deps)
    assert resp["statusCode"] == 200
    assert resp["headers"]["Content-Type"] == "text/csv"
    body = resp["body"]
    assert "rev4_control,rev4_title" in body
    assert "AC-1" in body and "AU-2" in body
    assert "ac.docx" in body and "au.docx" in body


def _seed_project_with_approved_draft(deps):
    project = Project(name="Sys", baseline=Baseline.FIPS_199_LOW)
    deps.repo.put_project(project)
    pid = project.project_id
    document = Document(project_id=pid, filename="ac.docx", s3_key="k")
    deps.repo.put_document(document)
    deps.repo.put_drafts(
        [
            Draft(
                project_id=pid,
                document_id=document.document_id,
                section_id="sec-1",
                order=0,
                rev4_control_ids=["AC-2"],
                rev5_control_ids=["AC-2"],
                draft_text="Approved Rev 5 language.",
                status=DraftStatus.APPROVED,
            )
        ]
    )
    return pid


def test_oscal_export_returns_component_definition(deps):
    pid = _seed_project_with_approved_draft(deps)
    resp = _oscal(_event(path={"project_id": pid}), deps)
    assert resp["statusCode"] == 200
    assert resp["headers"]["Content-Type"] == "application/json"
    assert "oscal-component" in resp["headers"]["Content-Disposition"]
    body = json.loads(resp["body"])
    reqs = body["component-definition"]["components"][0]["control-implementations"][0][
        "implemented-requirements"
    ]
    assert [r["control-id"] for r in reqs] == ["ac-2"]
    assert reqs[0]["description"] == "Approved Rev 5 language."


def test_oscal_export_404_missing_project(deps):
    with pytest.raises(HttpError) as exc:
        _oscal(_event(path={"project_id": "proj_missing"}), deps)
    assert exc.value.status == 404

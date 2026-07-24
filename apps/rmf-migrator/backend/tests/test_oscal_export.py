"""Tests for the OSCAL component-definition export.

The exporter turns a project's APPROVED Rev 5 drafts into a NIST OSCAL
component-definition (v1.1.2) that a GRC tool can import. It asserts on the
stable shape of the document and that Rev 4 -> Rev 5 provenance survives.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from rmf_migrator.common.models import (
    Baseline,
    DispositionNote,
    Draft,
    DraftStatus,
    Project,
)
from rmf_migrator.services.oscal_export import build_component_definition


def _project() -> Project:
    return Project(name="Widget System", baseline=Baseline.FIPS_199_MODERATE)


def _approved_draft(project: Project, *, section: str, rev4, rev5, text="Rev5 policy text."):
    return Draft(
        project_id=project.project_id,
        document_id="doc-1",
        section_id=section,
        order=0,
        rev4_control_ids=list(rev4),
        rev5_control_ids=list(rev5),
        dispositions=[
            DispositionNote(rev4_id=r, rev5_ids=list(rev5), relationship="renamed") for r in rev4
        ],
        draft_text=text,
        status=DraftStatus.APPROVED,
        reviewed_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
    )


def test_top_level_is_component_definition():
    project = _project()
    doc = build_component_definition(
        project, [_approved_draft(project, section="s1", rev4=["AC-1"], rev5=["AC-1"])]
    )
    assert "component-definition" in doc
    cd = doc["component-definition"]
    assert cd["uuid"]
    assert cd["metadata"]["oscal-version"] == "1.1.2"
    assert project.name in cd["metadata"]["title"]
    # last-modified derived from the approved draft's review time (not wall clock).
    assert cd["metadata"]["last-modified"].startswith("2026-06-01T12:00:00")
    assert len(cd["components"]) == 1


def test_control_implementation_has_requirement_per_control():
    project = _project()
    doc = build_component_definition(
        project, [_approved_draft(project, section="s1", rev4=["AC-2"], rev5=["AC-2"])]
    )
    ci = doc["component-definition"]["components"][0]["control-implementations"][0]
    assert ci["uuid"] and ci["source"]
    reqs = ci["implemented-requirements"]
    assert [r["control-id"] for r in reqs] == ["ac-2"]
    assert reqs[0]["description"] == "Rev5 policy text."


def test_control_id_uses_oscal_enhancement_form():
    project = _project()
    doc = build_component_definition(
        project, [_approved_draft(project, section="s1", rev4=["AC-2(1)"], rev5=["AC-2(1)"])]
    )
    reqs = doc["component-definition"]["components"][0]["control-implementations"][0][
        "implemented-requirements"
    ]
    assert reqs[0]["control-id"] == "ac-2.1"


def test_only_approved_drafts_are_exported():
    project = _project()
    approved = _approved_draft(project, section="s1", rev4=["AC-1"], rev5=["AC-1"])
    proposed = _approved_draft(project, section="s2", rev4=["AU-2"], rev5=["AU-2"])
    proposed.status = DraftStatus.PROPOSED
    doc = build_component_definition(project, [approved, proposed])
    reqs = doc["component-definition"]["components"][0]["control-implementations"][0][
        "implemented-requirements"
    ]
    ids = {r["control-id"] for r in reqs}
    assert ids == {"ac-1"}


def test_requirement_records_rev4_provenance():
    project = _project()
    draft = _approved_draft(project, section="s1", rev4=["AC-13"], rev5=["AC-2"])
    draft.dispositions = [DispositionNote(rev4_id="AC-13", rev5_ids=["AC-2"], relationship="split")]
    doc = build_component_definition(project, [draft])
    req = doc["component-definition"]["components"][0]["control-implementations"][0][
        "implemented-requirements"
    ][0]
    props = {(p["name"], p["value"]) for p in req["props"]}
    assert ("rev4-source", "AC-13") in props
    assert ("disposition", "split") in props


def test_multiple_drafts_for_one_control_merge():
    project = _project()
    d1 = _approved_draft(
        project, section="s1", rev4=["AC-2"], rev5=["AC-2"], text="First statement."
    )
    d2 = _approved_draft(
        project, section="s2", rev4=["AC-2"], rev5=["AC-2"], text="Second statement."
    )
    doc = build_component_definition(project, [d1, d2])
    reqs = doc["component-definition"]["components"][0]["control-implementations"][0][
        "implemented-requirements"
    ]
    assert len(reqs) == 1
    assert reqs[0]["control-id"] == "ac-2"
    assert "First statement." in reqs[0]["description"]
    assert "Second statement." in reqs[0]["description"]


def test_edited_text_wins_over_draft_text():
    project = _project()
    draft = _approved_draft(project, section="s1", rev4=["AC-1"], rev5=["AC-1"], text="LLM text.")
    draft.edited_text = "Human-edited text."
    doc = build_component_definition(project, [draft])
    req = doc["component-definition"]["components"][0]["control-implementations"][0][
        "implemented-requirements"
    ][0]
    assert req["description"] == "Human-edited text."


def test_uuids_are_deterministic():
    project = _project()
    drafts = [_approved_draft(project, section="s1", rev4=["AC-1"], rev5=["AC-1"])]
    a = build_component_definition(project, drafts)
    b = build_component_definition(project, drafts)
    assert a == b  # fully reproducible: no random uuids, no wall-clock timestamps


def test_output_is_json_serializable():
    project = _project()
    doc = build_component_definition(
        project, [_approved_draft(project, section="s1", rev4=["AC-1"], rev5=["AC-1"])]
    )
    text = json.dumps(doc)
    assert "component-definition" in text


def test_empty_when_no_approved_drafts():
    project = _project()
    doc = build_component_definition(project, [])
    cd = doc["component-definition"]
    ci = cd["components"][0]["control-implementations"][0]
    assert ci["implemented-requirements"] == []
    # Still a valid document with a metadata timestamp (falls back to project creation).
    assert cd["metadata"]["last-modified"]

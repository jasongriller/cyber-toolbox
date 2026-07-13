"""Tests for the decision-log builder + CSV export."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from rmf_migrator.common.models import (
    ControlMapping,
    DispositionNote,
    Draft,
    DraftStatus,
    MappingStatus,
    Section,
)
from rmf_migrator.services.decision_log import COLUMNS, build_rows, to_csv


def _section(sid, order, heading):
    return Section(
        section_id=sid, document_id="d", project_id="p", order=order, level=1, heading=heading
    )


def test_build_rows_joins_mapping_and_draft():
    sections = [_section("s1", 0, "AC Policy")]
    mappings = [
        ControlMapping(
            project_id="p",
            document_id="d",
            section_id="s1",
            order=0,
            final_control_ids=["AC-1"],
            status=MappingStatus.APPROVED,
            reviewed_by="jdoe",
            reviewed_at=datetime(2026, 7, 13, tzinfo=UTC),
        )
    ]
    drafts = [
        Draft(
            project_id="p",
            document_id="d",
            section_id="s1",
            order=0,
            rev4_control_ids=["AC-1"],
            rev5_control_ids=["AC-1"],
            dispositions=[
                DispositionNote(rev4_id="AC-1", rev5_ids=["AC-1"], relationship="renamed")
            ],
            edited_text="final",
            status=DraftStatus.APPROVED,
            reviewed_by="boss",
        )
    ]
    rows = build_rows(sections, mappings, drafts)
    assert len(rows) == 1
    row = rows[0]
    assert row["rev4_controls"] == "AC-1"
    assert row["rev5_controls"] == "AC-1"
    assert "renamed" in row["disposition"]
    assert row["mapping_status"] == "approved"
    assert row["mapping_reviewed_by"] == "jdoe"
    assert row["draft_status"] == "approved"
    assert row["draft_finalized"] == "yes"


def test_build_rows_sorted_by_order():
    sections = [_section("s2", 1, "B"), _section("s1", 0, "A")]
    rows = build_rows(sections, [], [])
    assert [r["order"] for r in rows] == ["0", "1"]


def test_build_rows_handles_missing_mapping_and_draft():
    rows = build_rows([_section("s1", 0, "H")], [], [])
    row = rows[0]
    assert row["rev4_controls"] == ""
    assert row["rev5_controls"] == ""
    assert row["draft_finalized"] == "no"


def test_to_csv_roundtrips():
    sections = [_section("s1", 0, "AC Policy")]
    rows = build_rows(sections, [], [])
    text = to_csv(rows)
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert list(parsed[0].keys()) == COLUMNS
    assert parsed[0]["heading"] == "AC Policy"


def test_to_csv_escapes_commas_in_heading():
    sections = [_section("s1", 0, "Policy, Procedures, and Scope")]
    text = to_csv(build_rows(sections, [], []))
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert parsed[0]["heading"] == "Policy, Procedures, and Scope"

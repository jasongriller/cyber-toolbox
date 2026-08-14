"""Tests for the eMASS control-implementation export.

One row per Rev 5 control the package implements, with the approved narrative
and the artifact (exported Rev 5 .docx) eMASS users attach — the bridge between
this tool's output and an eMASS system record under NIST 800-53 Rev 5.
"""

from __future__ import annotations

from datetime import UTC, datetime

from rmf_migrator.common.models import Draft, DraftStatus
from rmf_migrator.services.emass_export import COLUMNS, DraftSource, build_rows, to_csv


def _draft(
    section_id: str,
    rev5_ids: list[str],
    text: str,
    *,
    status: DraftStatus = DraftStatus.APPROVED,
    edited: str | None = None,
    reviewed_by: str | None = "isso",
    reviewed_at: datetime | None = None,
) -> Draft:
    return Draft(
        project_id="proj_1",
        document_id="doc_1",
        section_id=section_id,
        order=0,
        rev5_control_ids=rev5_ids,
        draft_text=text,
        edited_text=edited,
        status=status,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at or datetime(2026, 7, 1, tzinfo=UTC),
    )


def _source(draft: Draft, heading: str = "1. Account Management") -> DraftSource:
    return DraftSource(draft=draft, filename="ac-policy.docx", heading=heading)


def test_one_row_per_rev5_control_with_narrative_and_artifact():
    src = _source(_draft("s1", ["AC-2", "AC-3"], "Accounts are managed."))
    rows = build_rows([src])

    assert [r["control_id"] for r in rows] == ["AC-2", "AC-3"]
    ac2 = rows[0]
    assert "Accounts are managed." in ac2["implementation_narrative"]
    assert ac2["artifact"] == "ac-policy-rev5.docx"
    assert "ac-policy.docx" in ac2["source_sections"]
    assert "1. Account Management" in ac2["source_sections"]
    assert ac2["approved_by"] == "isso"


def test_control_titles_come_from_the_rev5_catalog():
    rows = build_rows([_source(_draft("s1", ["AC-2"], "text"))])
    assert rows[0]["control_title"] == "Account Management"


def test_only_approved_drafts_are_exported():
    approved = _source(_draft("s1", ["AC-2"], "approved text"))
    proposed = _source(_draft("s2", ["AU-2"], "unreviewed", status=DraftStatus.PROPOSED))
    rows = build_rows([approved, proposed])
    assert [r["control_id"] for r in rows] == ["AC-2"]


def test_multiple_sections_for_one_control_aggregate_into_one_row():
    a = _source(_draft("s1", ["AC-2"], "First narrative."), heading="1. Accounts")
    b = _source(_draft("s2", ["AC-2"], "Second narrative."), heading="2. Reviews")
    rows = build_rows([a, b])

    assert len(rows) == 1
    narrative = rows[0]["implementation_narrative"]
    assert "First narrative." in narrative and "Second narrative." in narrative
    assert "1. Accounts" in rows[0]["source_sections"]
    assert "2. Reviews" in rows[0]["source_sections"]


def test_edited_text_wins_over_llm_draft():
    src = _source(_draft("s1", ["AC-2"], "llm text", edited="human-approved text"))
    rows = build_rows([src])
    assert "human-approved text" in rows[0]["implementation_narrative"]
    assert "llm text" not in rows[0]["implementation_narrative"]


def test_controls_sort_in_family_then_numeric_order():
    rows = build_rows(
        [
            _source(_draft("s1", ["AC-10", "AC-2", "AC-2(1)", "AU-2"], "t")),
        ]
    )
    assert [r["control_id"] for r in rows] == ["AC-2", "AC-2(1)", "AC-10", "AU-2"]


def test_csv_is_formula_safe_and_has_stable_columns():
    src = DraftSource(
        draft=_draft("s1", ["AC-2"], "=HYPERLINK() injection attempt"),
        filename="=evil.docx",
        heading="=SUM(A1)",
    )
    csv_text = to_csv(build_rows([src]))
    lines = csv_text.strip().splitlines()
    assert lines[0] == ",".join(COLUMNS)
    assert "'=" in csv_text  # formula-prefixed cells neutralized

"""Drafting engine tests with a fake Bedrock and the real bundled crosswalk."""

from __future__ import annotations

from rmf_migrator.common.bedrock import BedrockError
from rmf_migrator.common.models import ControlMapping, DraftStatus, Section
from rmf_migrator.services.drafting import build_draft, draft_document


class FakeBedrock:
    def __init__(self, result=None, *, error: bool = False):
        self._result = result or {
            "draft_text": "Updated Rev 5 policy language.",
            "suggestions": ["Add review frequency.", "Name the responsible role."],
        }
        self._error = error
        self.last_user: str | None = None

    def converse_json(self, *, system: str, user: str, max_tokens: int = 2048):
        self.last_user = user
        if self._error:
            raise BedrockError("boom")
        return self._result


def _section(sid="sec_1", order=0, text="Accounts are managed.", heading="Account Management"):
    return Section(
        section_id=sid,
        document_id="doc_1",
        project_id="proj_1",
        order=order,
        level=2,
        heading=heading,
        text=text,
    )


def _mapping(sid="sec_1", order=0, control_ids=("AC-2",)):
    return ControlMapping(
        project_id="proj_1",
        document_id="doc_1",
        section_id=sid,
        order=order,
        proposed_control_ids=list(control_ids),
        final_control_ids=list(control_ids),
    )


def test_build_draft_resolves_rev5_targets_via_crosswalk():
    draft = build_draft(_section(), _mapping(control_ids=["AC-2"]), FakeBedrock())
    assert draft.rev4_control_ids == ["AC-2"]
    assert draft.rev5_control_ids == ["AC-2"]  # AC-2 persists into Rev 5
    assert draft.draft_text == "Updated Rev 5 policy language."
    assert len(draft.suggestions) == 2
    assert draft.status == DraftStatus.PROPOSED


def test_build_draft_records_disposition_relationship():
    # AC-1 is "renamed" Rev4 -> Rev5.
    draft = build_draft(_section(), _mapping(control_ids=["AC-1"]), FakeBedrock())
    assert len(draft.dispositions) == 1
    note = draft.dispositions[0]
    assert note.rev4_id == "AC-1"
    assert note.rev5_ids == ["AC-1"]
    assert note.relationship == "renamed"


def test_build_draft_handles_withdrawn_control():
    # SC-19 (VoIP) was withdrawn in Rev 5 with no successor -> no Rev 5 target.
    draft = build_draft(_section(), _mapping(control_ids=["SC-19"]), FakeBedrock())
    assert draft.rev5_control_ids == []
    assert draft.dispositions[0].relationship == "withdrawn"


def test_build_draft_carries_split_control_successors_forward():
    # AC-13 was incorporated into AC-2 + AU-6 -> both become drafting targets.
    draft = build_draft(_section(), _mapping(control_ids=["AC-13"]), FakeBedrock())
    assert set(draft.rev5_control_ids) == {"AC-2", "AU-6"}
    assert draft.dispositions[0].relationship == "split"


def test_build_draft_prompt_includes_targets_and_untrusted_markers():
    fake = FakeBedrock()
    build_draft(_section(), _mapping(control_ids=["AC-2"]), fake)
    assert "Rev 5 target control(s)" in fake.last_user
    assert "AC-2" in fake.last_user
    assert "UNTRUSTED ORIGINAL SECTION" in fake.last_user


def test_build_draft_resilient_on_bedrock_error():
    draft = build_draft(_section(), _mapping(), FakeBedrock(error=True))
    assert draft.draft_text == ""
    assert any("manually" in s for s in draft.suggestions)
    assert draft.status == DraftStatus.PROPOSED


def test_build_draft_dedupes_rev5_targets():
    # Two Rev 4 controls that both persist -> distinct Rev 5 ids, no dupes.
    draft = build_draft(_section(), _mapping(control_ids=["AC-2", "AC-3"]), FakeBedrock())
    assert draft.rev5_control_ids == ["AC-2", "AC-3"]


def test_draft_document_only_drafts_mapped_sections():
    sections = [_section(sid="sec_1", order=0), _section(sid="sec_2", order=1)]
    mappings = [_mapping(sid="sec_1", order=0)]  # sec_2 has no mapping
    drafts = draft_document(sections, mappings, FakeBedrock())
    assert len(drafts) == 1
    assert drafts[0].section_id == "sec_1"


def test_draft_document_preserves_order():
    sections = [_section(sid=f"sec_{i}", order=i) for i in range(3)]
    mappings = [_mapping(sid=f"sec_{i}", order=i) for i in range(3)]
    drafts = draft_document(sections, mappings, FakeBedrock())
    assert [d.order for d in drafts] == [0, 1, 2]

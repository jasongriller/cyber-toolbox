"""Tests for the catalog loader against the real bundled NIST data.

These assert on stable, well-known facts about SP 800-53 (AC-1 exists, the SR
family is new in Rev 5, AC-13 was withdrawn) so they validate both the loader and
that the committed data is sane — without pinning exact counts that shift if the
data is regenerated from a newer OSCAL release.
"""

from __future__ import annotations

from rmf_migrator.common.catalog import crosswalk, rev4_catalog, rev5_catalog


def test_rev4_catalog_loads_known_controls():
    cat = rev4_catalog()
    assert "AC-1" in cat
    assert "AC-2" in cat
    assert cat.get("AC-1").family == "AC"
    assert cat.get("AC-1").title  # non-empty


def test_rev5_has_supply_chain_family_new_in_rev5():
    cat = rev5_catalog()
    assert "SR-1" in cat  # Supply Chain Risk Management — added in Rev 5
    assert "SR" in cat.families()


def test_rev4_does_not_have_sr_family():
    assert "SR-1" not in rev4_catalog()


def test_validate_ids_splits_known_and_unknown():
    known, unknown = rev4_catalog().validate_ids(["AC-1", "ZZ-99", "AU-2"])
    assert known == ["AC-1", "AU-2"]
    assert unknown == ["ZZ-99"]


def test_enhancements_are_flagged():
    cat = rev5_catalog()
    base = cat.get("AC-2")
    enh = cat.get("AC-2(1)")
    assert base is not None and base.is_enhancement is False
    assert enh is not None and enh.is_enhancement is True


def test_crosswalk_ac1_is_renamed():
    # AC-1 kept its id but its title changed in Rev 5 -> "renamed".
    row = crosswalk().disposition("AC-1")
    assert row is not None
    assert row.rev5_ids == ("AC-1",)
    assert row.relationship == "renamed"


def test_crosswalk_reports_withdrawn_control():
    # SC-19 (VoIP) was withdrawn in Rev 5 with no successor control.
    row = crosswalk().disposition("SC-19")
    assert row is not None
    assert row.relationship == "withdrawn"
    assert row.rev5_ids == ()


def test_crosswalk_split_control_carries_successors():
    # AC-13 was incorporated into two Rev 5 controls -> "split", not a dead end.
    row = crosswalk().disposition("AC-13")
    assert row is not None
    assert row.relationship == "split"
    assert set(row.rev5_ids) == {"AC-2", "AU-6"}


def test_crosswalk_incorporated_control_carries_single_successor():
    # CA-4 was incorporated into CA-2 -> "incorporated".
    row = crosswalk().disposition("CA-4")
    assert row is not None
    assert row.relationship == "incorporated"
    assert row.rev5_ids == ("CA-2",)


def test_crosswalk_moved_control_carries_new_id():
    # AU-15 was renumbered to AU-5(5) in Rev 5 -> "moved".
    row = crosswalk().disposition("AU-15")
    assert row is not None
    assert row.relationship == "moved"
    assert row.rev5_ids == ("AU-5(5)",)


def test_crosswalk_new_in_rev5_includes_sr_family():
    new_ids = crosswalk().new_in_rev5()
    assert any(cid.startswith("SR-") for cid in new_ids)


def test_crosswalk_rev5_for_same_control():
    assert crosswalk().rev5_for("AC-2") == ["AC-2"]


def test_crosswalk_predecessors_links_new_control_to_rev4_origin():
    # SR-5 is new in Rev 5; SA-12(1) was renumbered into it, so a reverse
    # lookup recovers the Rev 4 origin the "new" row alone does not show.
    assert crosswalk().predecessors("SR-5") == ["SA-12(1)"]


def test_crosswalk_predecessors_aggregates_and_sorts():
    # Several Rev 4 controls fold into AC-2 (its own same-id row, plus splits
    # and an incorporated enhancement); all are returned, unique and sorted.
    preds = crosswalk().predecessors("AC-2")
    assert preds == sorted(set(preds))
    assert {"AC-2", "AC-13", "SC-14"} <= set(preds)


def test_crosswalk_predecessors_unknown_control_is_empty():
    assert crosswalk().predecessors("ZZ-99") == []

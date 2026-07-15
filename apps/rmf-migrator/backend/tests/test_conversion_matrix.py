"""Conversion matrix builder tests."""

from __future__ import annotations

import csv
import io

from rmf_migrator.services.conversion_matrix import COLUMNS, Contribution, build_rows, to_csv


def test_build_rows_groups_locations_by_control():
    contributions = [
        Contribution("AC-1", "ac-policy.docx", "Access Control Policy"),
        Contribution("AC-1", "master.docx", "AC Section"),
        Contribution("AC-2", "ac-policy.docx", "Account Management"),
    ]
    rows = {r["rev4_control"]: r for r in build_rows(contributions)}
    assert "ac-policy.docx: Access Control Policy" in rows["AC-1"]["covered_in"]
    assert "master.docx: AC Section" in rows["AC-1"]["covered_in"]
    assert rows["AC-2"]["covered_in"] == "ac-policy.docx: Account Management"


def test_build_rows_enriches_disposition_and_rev5():
    rows = {r["rev4_control"]: r for r in build_rows([Contribution("AC-1", "d.docx", "H")])}
    assert rows["AC-1"]["disposition"] == "renamed"  # AC-1 title changed in Rev 5
    assert rows["AC-1"]["rev5_controls"] == "AC-1"
    assert rows["AC-1"]["rev4_title"]  # non-empty


def test_build_rows_marks_withdrawn():
    rows = {r["rev4_control"]: r for r in build_rows([Contribution("SC-19", "d.docx", "H")])}
    assert rows["SC-19"]["disposition"] == "withdrawn"
    assert rows["SC-19"]["rev5_controls"] == ""


def test_build_rows_marks_split_with_successors():
    # AC-13 was incorporated into two Rev 5 controls -> disposition "split".
    rows = {r["rev4_control"]: r for r in build_rows([Contribution("AC-13", "d.docx", "H")])}
    assert rows["AC-13"]["disposition"] == "split"
    assert rows["AC-13"]["rev5_controls"] == "AC-2, AU-6"


def test_build_rows_sorted():
    rows = build_rows([Contribution("AU-2", "d", "h"), Contribution("AC-1", "d", "h")])
    assert [r["rev4_control"] for r in rows] == ["AC-1", "AU-2"]


def test_build_rows_surfaces_mapping_source():
    # An editorial successor-link disposition is higher-confidence than a
    # mechanical id diff; the matrix records which each row came from.
    rows = {r["rev4_control"]: r for r in build_rows([Contribution("AC-13", "d", "H")])}
    assert rows["AC-13"]["source"] == "catalog:successor-links"
    rows = {r["rev4_control"]: r for r in build_rows([Contribution("AC-1", "d", "H")])}
    assert rows["AC-1"]["source"] == "derived:id-diff"


def test_build_rows_flags_targets_outside_baseline():
    # SA-6 split -> CM-10 (in Low) + SI-7 (not in Low); only the out-of-baseline
    # target is flagged so an assessor knows the successor is not required here.
    built = build_rows([Contribution("SA-6", "d", "H")], baseline="low")
    rows = {r["rev4_control"]: r for r in built}
    assert rows["SA-6"]["targets_outside_baseline"] == "SI-7"


def test_build_rows_without_baseline_leaves_flag_empty():
    rows = {r["rev4_control"]: r for r in build_rows([Contribution("SA-6", "d", "H")])}
    assert rows["SA-6"]["targets_outside_baseline"] == ""


def test_to_csv_has_headers_and_parses():
    text = to_csv(build_rows([Contribution("AC-1", "d.docx", "H")]))
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert list(parsed[0].keys()) == COLUMNS
    assert parsed[0]["rev4_control"] == "AC-1"

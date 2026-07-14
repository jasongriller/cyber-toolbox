"""Tests for CSV formula-injection neutralizing.

The decision log and conversion matrix carry text lifted straight out of an
uploaded document, and both are opened in Excel by an assessor.
"""

from __future__ import annotations

import pytest

from rmf_migrator.common.csv_safe import csv_safe, csv_safe_row
from rmf_migrator.services import conversion_matrix, decision_log
from rmf_migrator.services.conversion_matrix import Contribution


@pytest.mark.parametrize(
    "payload",
    [
        '=HYPERLINK("http://attacker.example/x","click")',
        "+1+1",
        "-1+1",
        "@SUM(A1:A9)",
        "=cmd|'/c calc'!A0",
    ],
)
def test_formula_prefixes_are_neutralized(payload):
    assert csv_safe(payload) == "'" + payload


@pytest.mark.parametrize("payload", ["\t=1+1", "\r=1+1", "\n=1+1"])
def test_leading_whitespace_before_formula_is_neutralized(payload):
    """Excel strips a leading tab/CR, then re-tests the first character."""
    assert csv_safe(payload).startswith("'")


@pytest.mark.parametrize("value", ["", "AC-2", "Access Control Policy", "a=b"])
def test_ordinary_values_are_untouched(value):
    assert csv_safe(value) == value


def test_csv_safe_row_covers_every_column():
    row = {"heading": "=1+1", "order": "3"}
    assert csv_safe_row(row) == {"heading": "'=1+1", "order": "3"}


def test_decision_log_csv_neutralizes_a_malicious_heading():
    from rmf_migrator.common.models import Section

    section = Section(
        section_id="sec_1",
        document_id="doc_1",
        project_id="proj_1",
        order=0,
        level=1,
        heading='=HYPERLINK("http://attacker.example","click")',
        text="x",
    )
    csv_text = decision_log.to_csv(decision_log.build_rows([section], [], []))
    assert "\"'=HYPERLINK" in csv_text
    # The raw formula must never appear at the start of a cell.
    assert ",=HYPERLINK" not in csv_text


def test_conversion_matrix_csv_neutralizes_a_malicious_heading_and_filename():
    contributions = [
        Contribution(rev4_id="AC-2", filename="=1+1.docx", heading="=1+1"),
    ]
    csv_text = conversion_matrix.to_csv(conversion_matrix.build_rows(contributions))
    assert "'=1+1" in csv_text

"""Tests for DOCX section extraction.

The parser is split so its core logic operates on a *paragraph stream* — an
iterable of ``Paragraph(style, text)`` — independent of python-docx. These tests
drive that core directly with synthetic streams (no .docx fixtures needed).
A separate adapter converts a real python-docx Document into that stream and is
covered by ``test_docx_adapter`` which is skipped when python-docx is absent.
"""

from __future__ import annotations

import pytest

from rmf_migrator.docx.parser import Paragraph, parse_paragraph_stream


def _p(style: str, text: str) -> Paragraph:
    return Paragraph(style=style, text=text)


def test_empty_document_yields_no_sections():
    assert parse_paragraph_stream([], document_id="doc_1", project_id="proj_1") == []


def test_preamble_before_first_heading_becomes_level0_section():
    stream = [
        _p("Normal", "This policy is issued under authority X."),
        _p("Normal", "Effective immediately."),
        _p("Heading 1", "Access Control Policy"),
        _p("Normal", "The organization controls access."),
    ]
    sections = parse_paragraph_stream(stream, document_id="doc_1", project_id="proj_1")

    assert sections[0].level == 0
    assert sections[0].heading == ""
    assert "authority X" in sections[0].text
    assert "Effective immediately." in sections[0].text
    assert sections[0].parent_id is None


def test_heading_and_body_extraction():
    stream = [
        _p("Heading 1", "Access Control Policy"),
        _p("Normal", "First paragraph."),
        _p("Normal", "Second paragraph."),
    ]
    sections = parse_paragraph_stream(stream, document_id="doc_1", project_id="proj_1")

    section = sections[0]
    assert section.heading == "Access Control Policy"
    assert section.level == 1
    assert section.text == "First paragraph.\nSecond paragraph."
    assert section.char_length == len(section.text)


def test_nested_headings_set_parent_ids():
    stream = [
        _p("Heading 1", "AC Policy"),
        _p("Normal", "intro"),
        _p("Heading 2", "AC-2 Account Management"),
        _p("Normal", "accounts detail"),
        _p("Heading 2", "AC-3 Access Enforcement"),
        _p("Normal", "enforcement detail"),
    ]
    sections = parse_paragraph_stream(stream, document_id="doc_1", project_id="proj_1")

    by_heading = {s.heading: s for s in sections}
    ac = by_heading["AC Policy"]
    ac2 = by_heading["AC-2 Account Management"]
    ac3 = by_heading["AC-3 Access Enforcement"]

    assert ac.level == 1 and ac.parent_id is None
    assert ac2.level == 2 and ac2.parent_id == ac.section_id
    assert ac3.level == 2 and ac3.parent_id == ac.section_id


def test_deeper_nesting_resolves_to_nearest_ancestor():
    stream = [
        _p("Heading 1", "H1"),
        _p("Heading 2", "H2"),
        _p("Heading 3", "H3"),
        _p("Heading 2", "H2b"),  # pops back up: parent is H1, not H3
    ]
    sections = parse_paragraph_stream(stream, document_id="doc_1", project_id="proj_1")
    by_heading = {s.heading: s for s in sections}

    assert by_heading["H3"].parent_id == by_heading["H2"].section_id
    assert by_heading["H2b"].parent_id == by_heading["H1"].section_id


def test_order_is_monotonic_and_document_order():
    stream = [
        _p("Heading 1", "First"),
        _p("Heading 1", "Second"),
        _p("Heading 1", "Third"),
    ]
    sections = parse_paragraph_stream(stream, document_id="doc_1", project_id="proj_1")
    orders = [s.order for s in sections]
    assert orders == sorted(orders)
    assert orders == [0, 1, 2]


def test_blank_paragraphs_are_ignored_in_body():
    stream = [
        _p("Heading 1", "H1"),
        _p("Normal", "   "),
        _p("Normal", "real content"),
        _p("Normal", ""),
    ]
    sections = parse_paragraph_stream(stream, document_id="doc_1", project_id="proj_1")
    assert sections[0].text == "real content"


@pytest.mark.parametrize(
    "style,expected_level",
    [
        ("Heading 1", 1),
        ("Heading 2", 2),
        ("Heading 6", 6),
        ("heading 3", 3),  # case-insensitive
        ("Title", 1),  # Title is treated as a top-level heading
        ("Normal", None),
        ("Body Text", None),
        ("List Bullet", None),
    ],
)
def test_heading_level_detection(style, expected_level):
    from rmf_migrator.docx.parser import heading_level

    assert heading_level(style) == expected_level


def test_ids_reference_this_document_and_project():
    stream = [_p("Heading 1", "H1")]
    sections = parse_paragraph_stream(stream, document_id="doc_XYZ", project_id="proj_ABC")
    assert sections[0].document_id == "doc_XYZ"
    assert sections[0].project_id == "proj_ABC"

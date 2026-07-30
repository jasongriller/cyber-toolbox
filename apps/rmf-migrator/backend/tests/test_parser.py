"""Tests for DOCX section extraction.

The parser is split so its core logic operates on a *paragraph stream* — an
iterable of ``Paragraph(style, text)`` — independent of python-docx. These tests
drive that core directly with synthetic streams (no .docx fixtures needed).
A separate adapter converts a real python-docx Document into that stream and is
covered by ``test_docx_adapter`` which is skipped when python-docx is absent.
"""

from __future__ import annotations

import io

import pytest
from docx import Document as DocxDocument

from rmf_migrator.docx.parser import Paragraph, parse_docx_bytes, parse_paragraph_stream


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


def test_docx_adapter_parses_paragraphs_inside_content_controls():
    """DoD/CMS templates wrap sections in w:sdt content controls; their
    paragraphs must not be silently dropped."""
    from docx.oxml.ns import qn

    doc = DocxDocument()
    doc.add_heading("Identification and Authentication Policy", level=1)
    heading = doc.add_heading("IA-5 Authenticator Management", level=2)
    body = doc.add_paragraph("Initial authenticators are delivered out of band.")

    root = doc.element.body
    sdt = root.makeelement(qn("w:sdt"), {})
    sdt_content = root.makeelement(qn("w:sdtContent"), {})
    sdt.append(root.makeelement(qn("w:sdtPr"), {}))
    sdt.append(sdt_content)
    root.replace(heading._p, sdt)  # noqa: SLF001
    sdt_content.append(heading._p)  # noqa: SLF001
    sdt_content.append(body._p)  # noqa: SLF001

    buf = io.BytesIO()
    doc.save(buf)
    sections = parse_docx_bytes(buf.getvalue(), document_id="doc_1", project_id="proj_1")

    headings = [s.heading for s in sections]
    assert "IA-5 Authenticator Management" in headings
    joined = "\n".join(s.text for s in sections)
    assert "out of band" in joined


def _style_with_outline_level(doc, name: str, level: int):
    from docx.enum.style import WD_STYLE_TYPE
    from docx.oxml.ns import qn

    style = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    style.base_style = doc.styles["Normal"]
    ppr = style.element.get_or_add_pPr()
    ppr.append(ppr.makeelement(qn("w:outlineLvl"), {qn("w:val"): str(level - 1)}))
    return style


def test_docx_adapter_detects_headings_by_style_outline_level():
    """Localized Word ("Überschrift 1") and org templates ("PolicyHead 1")
    don't match the english style-name regex, but their styles carry
    w:outlineLvl — the language-invariant heading signal."""
    doc = DocxDocument()
    _style_with_outline_level(doc, "Überschrift 1", 1)
    _style_with_outline_level(doc, "Überschrift 2", 2)
    doc.add_paragraph("Kontenverwaltung", style="Überschrift 1")
    doc.add_paragraph("Konten werden vierteljährlich überprüft.")
    doc.add_paragraph("Automatisierte Verwaltung", style="Überschrift 2")
    doc.add_paragraph("Inaktive Konten werden nach 90 Tagen deaktiviert.")

    buf = io.BytesIO()
    doc.save(buf)
    sections = parse_docx_bytes(buf.getvalue(), document_id="doc_1", project_id="proj_1")

    by_heading = {s.heading: s for s in sections}
    assert by_heading["Kontenverwaltung"].level == 1
    assert by_heading["Automatisierte Verwaltung"].level == 2
    assert (
        by_heading["Automatisierte Verwaltung"].parent_id
        == by_heading["Kontenverwaltung"].section_id
    )


def test_docx_adapter_detects_headings_by_paragraph_outline_level():
    """Some converters put w:outlineLvl directly on the paragraph, style Normal."""
    from docx.oxml.ns import qn

    doc = DocxDocument()
    para = doc.add_paragraph("Media Sanitization")
    ppr = para._p.get_or_add_pPr()  # noqa: SLF001
    ppr.append(ppr.makeelement(qn("w:outlineLvl"), {qn("w:val"): "0"}))
    doc.add_paragraph("Media is sanitized prior to disposal.")

    buf = io.BytesIO()
    doc.save(buf)
    sections = parse_docx_bytes(buf.getvalue(), document_id="doc_1", project_id="proj_1")

    assert sections[0].heading == "Media Sanitization"
    assert sections[0].level == 1
    assert sections[0].text == "Media is sanitized prior to disposal."


def test_docx_adapter_keeps_tracked_insertions_and_drops_deletions():
    """Documents circulated for review carry unaccepted revisions: pending
    insertions (w:ins) are what the document will say — keep them; deleted
    text (w:del/w:delText) is going away — drop it."""
    from docx.oxml.ns import qn

    doc = DocxDocument()
    doc.add_heading("Personnel Screening", level=1)
    para = doc.add_paragraph("Individuals are screened before access")

    ins = para._p.makeelement(  # noqa: SLF001
        qn("w:ins"), {qn("w:id"): "1", qn("w:author"): "Reviewer"}
    )
    run = para.add_run(" and rescreened every five years.")
    para._p.remove(run._r)  # noqa: SLF001
    ins.append(run._r)  # noqa: SLF001
    para._p.append(ins)  # noqa: SLF001

    deleted = para._p.makeelement(  # noqa: SLF001
        qn("w:del"), {qn("w:id"): "2", qn("w:author"): "Reviewer"}
    )
    del_run = para._p.makeelement(qn("w:r"), {})  # noqa: SLF001
    del_text = para._p.makeelement(qn("w:delText"), {})  # noqa: SLF001
    del_text.text = "OBSOLETE TEXT"
    del_run.append(del_text)
    deleted.append(del_run)
    para._p.append(deleted)  # noqa: SLF001

    buf = io.BytesIO()
    doc.save(buf)
    sections = parse_docx_bytes(buf.getvalue(), document_id="doc_1", project_id="proj_1")

    joined = "\n".join(s.text for s in sections)
    assert "rescreened every five years" in joined
    assert "OBSOLETE TEXT" not in joined


def test_empty_heading_paragraphs_do_not_open_sections():
    """Real templates (e.g. SANS) sprinkle empty heading-styled paragraphs as
    vertical spacing; each one used to open a junk section with no heading and
    no body — noise in review and a wasted model call each."""
    stream = [
        _p("Heading 1", "Purpose"),
        _p("Normal", "The purpose of this policy."),
        _p("Heading 1", "   "),  # spacing paragraph, heading-styled
        _p("Heading 1", "Scope"),
        _p("Normal", "Applies to everyone."),
    ]
    sections = parse_paragraph_stream(stream, document_id="doc_1", project_id="proj_1")

    assert [s.heading for s in sections] == ["Purpose", "Scope"]
    assert sections[0].text == "The purpose of this policy."
    assert sections[1].text == "Applies to everyone."


def test_docx_adapter_includes_table_cell_text_in_document_order():
    doc = DocxDocument()
    doc.add_heading("Access Control Policy", level=1)
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).paragraphs[0].add_run("Table-based implementation detail.")
    doc.add_heading("AU-2 Audit Events", level=1)
    doc.add_paragraph("Audit body.")
    buf = io.BytesIO()
    doc.save(buf)

    sections = parse_docx_bytes(buf.getvalue(), document_id="doc_1", project_id="proj_1")

    assert [section.heading for section in sections] == [
        "Access Control Policy",
        "AU-2 Audit Events",
    ]
    assert sections[0].text == "Table-based implementation detail."
    assert sections[1].text == "Audit body."

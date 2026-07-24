"""Structure-preserving Rev 5 DOCX export ("docx surgery").

Produces a new .docx that keeps the original document's headings, styles, table
of contents, and boilerplate, replacing only the body of each mapped section with
its approved Rev 5 text. Sections are identified the same way the parser
identifies them, so section orders line up and unmapped content is left untouched.

Approach: walk the document's paragraphs into sections (preamble + one per
heading), exactly mirroring ``parse_paragraph_stream``'s ordering. For each
section whose order has replacement text, delete the section's existing body
paragraphs and insert new paragraphs (one per line) right after the heading
(or, for the preamble, before the first heading).
"""

from __future__ import annotations

import io

from docx import Document as DocxDocument

from rmf_migrator.common.limits import guard_docx_bytes

from .parser import heading_level, iter_docx_blocks


def _style_name(paragraph) -> str | None:  # noqa: ANN001 (python-docx type)
    return paragraph.style.name if paragraph.style is not None else None


def _is_heading(paragraph) -> bool:  # noqa: ANN001
    return heading_level(_style_name(paragraph)) is not None


def _walk_sections(document) -> list[dict]:  # noqa: ANN001
    """Split the document into ordered sections mirroring the parser.

    Each entry: {order, anchor, body}. ``anchor`` is the heading Paragraph (None
    for the preamble). ``body`` is the list of Paragraphs physically belonging to
    that section (excluding the heading). A preamble section exists only if there
    is non-empty text before the first heading — matching the parser.
    """
    preamble: list = []
    headings: list[tuple] = []  # (heading_paragraph, [body paragraphs])
    current_body: list | None = None

    for paragraph in iter_docx_blocks(document):
        if _is_heading(paragraph):
            body: list = []
            headings.append((paragraph, body))
            current_body = body
        elif current_body is None:
            preamble.append(paragraph)
        else:
            current_body.append(paragraph)

    sections: list[dict] = []
    order = 0
    if any(p.text.strip() for p in preamble):
        sections.append({"order": 0, "anchor": None, "body": preamble})
        order = 1
    for heading_paragraph, body in headings:
        sections.append({"order": order, "anchor": heading_paragraph, "body": body})
        order += 1
    return sections


def _detached_paragraph_element(document, text: str, style_name: str | None):  # noqa: ANN001
    """Create a paragraph, apply a style if valid, and detach its XML element."""
    paragraph = document.add_paragraph(text)
    if style_name:
        try:
            paragraph.style = style_name
        except (KeyError, ValueError):
            pass  # style not in this document; leave default
    element = paragraph._p  # noqa: SLF001
    element.getparent().remove(element)  # detach from the end of the body
    return element


def _body_style(body: list) -> str | None:
    for paragraph in body:
        if paragraph.text.strip():
            return _style_name(paragraph)
    return None


def _first_heading(document):  # noqa: ANN001
    for paragraph in iter_docx_blocks(document):
        if _is_heading(paragraph):
            return paragraph
    return None


def _replace_section_body(document, section: dict, text: str) -> None:  # noqa: ANN001
    body = section["body"]
    style_name = _body_style(body)
    lines = [line for line in text.split("\n") if line.strip() != ""]

    # Reuse existing paragraph locations first. This preserves table cells and
    # other block structure instead of deleting their required paragraph nodes.
    if body:
        for index, paragraph in enumerate(body):
            paragraph.text = lines[index] if index < len(lines) else ""
        if len(lines) > len(body):
            cursor = body[-1]._p  # noqa: SLF001
            for line in lines[len(body) :]:
                element = _detached_paragraph_element(document, line, style_name)
                cursor.addnext(element)
                cursor = element
        return

    new_elements = [_detached_paragraph_element(document, line, style_name) for line in lines]

    anchor = section["anchor"]
    if anchor is not None:
        cursor = anchor._p  # noqa: SLF001
        for element in new_elements:
            cursor.addnext(element)
            cursor = element
    else:
        heading = _first_heading(document)
        if heading is not None:
            prev = None
            for element in new_elements:
                if prev is None:
                    heading._p.addprevious(element)  # noqa: SLF001
                else:
                    prev.addnext(element)
                prev = element
        else:
            body_el = document.element.body
            for element in new_elements:
                body_el.append(element)


def export_rev5_docx(original_bytes: bytes, drafts_by_order: dict[int, str]) -> bytes:
    """Return a new .docx with mapped section bodies replaced by Rev 5 text.

    ``drafts_by_order`` maps a section's order (as produced by the parser) to the
    replacement text. Sections whose order is absent are left unchanged.
    """
    guard_docx_bytes(original_bytes)
    document = DocxDocument(io.BytesIO(original_bytes))

    for section in _walk_sections(document):
        if section["order"] in drafts_by_order:
            _replace_section_body(document, section, drafts_by_order[section["order"]])

    out = io.BytesIO()
    document.save(out)
    return out.getvalue()

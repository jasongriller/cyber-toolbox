"""DOCX section extraction.

The parser turns a Word document into an ordered, nested list of ``Section``s
that later milestones map to controls. It is split into two layers:

* ``parse_paragraph_stream`` — pure logic over an iterable of ``Paragraph``
  (style + text). This holds all the section/nesting rules and is fully unit
  tested without any .docx dependency.
* ``iter_docx_paragraphs`` / ``parse_docx_bytes`` — a thin adapter that reads a
  real .docx (via python-docx) into that stream.

Design choices:
* Headings are detected by paragraph style name ("Heading 1".."Heading 9",
  plus "Title" as top level). This matches how policy templates are authored.
* Style names are an English-Word convention, so ``w:outlineLvl`` (on the
  paragraph or its style chain) is used as a language- and template-invariant
  fallback: localized Word ("Überschrift 1") and org templates ("PolicyHead")
  carry it even though their names never match the regex.
* Text before the first heading is captured as a synthetic level-0 preamble
  section so nothing is lost.
* Parent is the nearest preceding section with a strictly smaller level, so
  jumping from H3 back to H2 correctly re-parents under the enclosing H1.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from rmf_migrator.common.models import Section

_HEADING_RE = re.compile(r"^heading\s+([1-9])$", re.IGNORECASE)


@dataclass(frozen=True)
class Paragraph:
    style: str
    text: str
    # 1-based heading depth from w:outlineLvl, when the document carries one.
    # None means "no outline information" — style-name detection still applies.
    outline_level: int | None = None


def heading_level(style: str | None) -> int | None:
    """Return heading depth (1-based) for a style name, or None if not a heading."""
    if not style:
        return None
    normalized = style.strip()
    if normalized.lower() == "title":
        return 1
    match = _HEADING_RE.match(normalized)
    return int(match.group(1)) if match else None


def parse_paragraph_stream(
    paragraphs: Iterable[Paragraph], *, document_id: str, project_id: str
) -> list[Section]:
    """Convert a paragraph stream into ordered, nested sections."""
    sections: list[Section] = []
    # Stack of (level, section) for open ancestors, used to resolve parents.
    ancestors: list[tuple[int, Section]] = []
    current: Section | None = None
    body_lines: list[str] = []
    order = 0

    def flush_body() -> None:
        if current is not None:
            text = "\n".join(body_lines)
            current.text = text
            current.char_length = len(text)

    for para in paragraphs:
        level = heading_level(para.style)
        if level is None:
            level = para.outline_level
        if level is not None and not para.text.strip():
            # Empty heading-styled paragraphs are vertical spacing in real
            # templates; opening a section for each adds review noise and a
            # wasted model call. Skip them without disturbing the open body.
            continue
        if level is None:
            line = para.text.strip()
            if not line:
                continue
            if current is None:
                # Preamble before any heading -> synthetic level-0 section.
                current = Section(
                    document_id=document_id,
                    project_id=project_id,
                    order=order,
                    level=0,
                    heading="",
                    parent_id=None,
                )
                order += 1
                sections.append(current)
                body_lines = []
            body_lines.append(line)
            continue

        # New heading: close out the previous section's body first.
        flush_body()

        # Pop ancestors that are at or below this heading's level.
        while ancestors and ancestors[-1][0] >= level:
            ancestors.pop()
        parent_id = ancestors[-1][1].section_id if ancestors else None

        current = Section(
            document_id=document_id,
            project_id=project_id,
            order=order,
            level=level,
            heading=para.text.strip(),
            parent_id=parent_id,
        )
        order += 1
        sections.append(current)
        ancestors.append((level, current))
        body_lines = []

    flush_body()
    return sections


def iter_docx_blocks(parent) -> Iterator:  # noqa: ANN001
    """Yield paragraphs from document bodies and table cells in reading order."""
    from docx.table import _Cell

    container = parent._tc if isinstance(parent, _Cell) else parent.element.body  # noqa: SLF001
    yield from _iter_block_children(container, parent)


def _iter_block_children(container, parent) -> Iterator:  # noqa: ANN001
    """Walk block-level children: paragraphs, tables, and w:sdt wrappers.

    Content controls (w:sdt) are how locked template regions are authored in
    DoD/CMS policy templates; their paragraphs live under w:sdtContent and are
    invisible to a plain CT_P scan — skipping them silently drops policy text.
    """
    from docx.oxml.ns import qn
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph as DocxParagraph

    for child in container.iterchildren():
        if isinstance(child, CT_P):
            yield DocxParagraph(child, parent)
        elif isinstance(child, CT_Tbl):
            table = Table(child, parent)
            seen_cells: set[int] = set()
            for row in table.rows:
                for cell in row.cells:
                    cell_id = id(cell._tc)  # noqa: SLF001
                    if cell_id in seen_cells:
                        continue
                    seen_cells.add(cell_id)
                    yield from iter_docx_blocks(cell)
        elif child.tag == qn("w:sdt"):
            for content in child.iterchildren(qn("w:sdtContent")):
                yield from _iter_block_children(content, parent)


def _run_text(run_element) -> str:  # noqa: ANN001
    """Text of one w:r: literal text plus tab/break characters."""
    from docx.oxml.ns import qn

    parts: list[str] = []
    for child in run_element.iterchildren():
        if child.tag == qn("w:t"):
            parts.append(child.text or "")
        elif child.tag == qn("w:tab"):
            parts.append("\t")
        elif child.tag in {qn("w:br"), qn("w:cr")}:
            parts.append("\n")
    return "".join(parts)


def _element_text(element) -> str:  # noqa: ANN001
    """Paragraph text, revision- and wrapper-aware.

    python-docx's ``.text`` walks only direct w:r children, which loses runs
    inside pending insertions (w:ins) and inline content controls. This walks
    the containers a policy document actually uses, keeps pending insertions
    (they are what the document will say once revisions are accepted), and
    drops deleted text (w:del holds w:delText, never yielded here).
    """
    from docx.oxml.ns import qn

    parts: list[str] = []
    for child in element.iterchildren():
        tag = child.tag
        if tag == qn("w:r"):
            parts.append(_run_text(child))
        elif tag in {qn("w:hyperlink"), qn("w:ins"), qn("w:smartTag")}:
            parts.append(_element_text(child))
        elif tag == qn("w:sdt"):
            for content in child.iterchildren(qn("w:sdtContent")):
                parts.append(_element_text(content))
    return "".join(parts)


def _outline_from_ppr(ppr) -> int | None:  # noqa: ANN001
    """1-based heading depth from a w:pPr, or None. val 9 means body text."""
    from docx.oxml.ns import qn

    if ppr is None:
        return None
    element = ppr.find(qn("w:outlineLvl"))
    if element is None:
        return None
    try:
        val = int(element.get(qn("w:val")))
    except (TypeError, ValueError):
        return None
    return val + 1 if 0 <= val <= 8 else None


def _outline_level(para) -> int | None:  # noqa: ANN001
    """Outline level from the paragraph itself, then up its style chain."""
    from docx.oxml.ns import qn

    level = _outline_from_ppr(para._p.pPr)  # noqa: SLF001
    if level is not None:
        return level
    style = para.style
    for _ in range(10):  # basedOn chains are short; bound against cycles
        if style is None:
            return None
        level = _outline_from_ppr(style.element.find(qn("w:pPr")))
        if level is not None:
            return level
        style = style.base_style
    return None


def iter_docx_paragraphs(document) -> Iterator[Paragraph]:  # noqa: ANN001 (python-docx type)
    """Adapt a DOCX, including table cells, into a paragraph stream."""
    for para in iter_docx_blocks(document):
        style_name = para.style.name if para.style is not None else None
        yield Paragraph(
            style=style_name or "Normal",
            text=_element_text(para._p),  # noqa: SLF001
            outline_level=_outline_level(para),
        )


def parse_docx_bytes(data: bytes, *, document_id: str, project_id: str) -> list[Section]:
    """Parse raw .docx bytes into sections."""
    import io

    from docx import Document as DocxDocument  # imported lazily; heavy dependency

    from rmf_migrator.common.limits import guard_docx_bytes, guard_parsed_sections

    guard_docx_bytes(data)
    document = DocxDocument(io.BytesIO(data))
    sections = parse_paragraph_stream(
        iter_docx_paragraphs(document), document_id=document_id, project_id=project_id
    )
    guard_parsed_sections(sections)
    return sections

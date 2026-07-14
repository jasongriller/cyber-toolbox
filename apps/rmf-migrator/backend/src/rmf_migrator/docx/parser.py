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


def iter_docx_paragraphs(document) -> Iterator[Paragraph]:  # noqa: ANN001 (python-docx type)
    """Adapt a python-docx Document into a Paragraph stream in reading order."""
    for para in document.paragraphs:
        style_name = para.style.name if para.style is not None else None
        yield Paragraph(style=style_name or "Normal", text=para.text)


def parse_docx_bytes(data: bytes, *, document_id: str, project_id: str) -> list[Section]:
    """Parse raw .docx bytes into sections."""
    import io

    from docx import Document as DocxDocument  # imported lazily; heavy dependency

    from rmf_migrator.common.limits import guard_docx_bytes

    guard_docx_bytes(data)
    document = DocxDocument(io.BytesIO(data))
    return parse_paragraph_stream(
        iter_docx_paragraphs(document), document_id=document_id, project_id=project_id
    )

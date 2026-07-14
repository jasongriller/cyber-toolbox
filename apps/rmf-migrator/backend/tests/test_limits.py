"""Tests for the untrusted-.docx size guards (zip-bomb defense)."""

from __future__ import annotations

import io
import zipfile

import pytest
from docx import Document as DocxDocument

from rmf_migrator.common.limits import (
    MAX_COMPRESSION_RATIO,
    MAX_DOCX_BYTES,
    DocxTooLarge,
    guard_docx_bytes,
)
from rmf_migrator.docx.export_docx import export_rev5_docx
from rmf_migrator.docx.parser import parse_docx_bytes


def _real_docx() -> bytes:
    doc = DocxDocument()
    doc.add_heading("Access Control Policy", level=1)
    doc.add_paragraph("The organization manages accounts.")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _zip_bomb() -> bytes:
    """A small zip whose single member decompresses enormously."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b" " * (80 * 1024 * 1024))
    return buf.getvalue()


def test_real_document_passes_the_guard():
    guard_docx_bytes(_real_docx())  # must not raise


def test_zip_bomb_is_rejected_on_compression_ratio():
    data = _zip_bomb()
    assert len(data) < MAX_DOCX_BYTES  # small on the wire — that's the point
    with pytest.raises(DocxTooLarge):
        guard_docx_bytes(data)


def test_oversized_bytes_are_rejected():
    with pytest.raises(DocxTooLarge):
        guard_docx_bytes(b"x" * (MAX_DOCX_BYTES + 1))


def test_non_zip_bytes_are_rejected():
    with pytest.raises(DocxTooLarge):
        guard_docx_bytes(b"this is not a docx")


def test_ratio_constant_is_sane_for_real_documents():
    data = _real_docx()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        total = sum(i.file_size for i in archive.infolist())
    assert total / len(data) < MAX_COMPRESSION_RATIO


def test_parser_rejects_a_zip_bomb():
    with pytest.raises(DocxTooLarge):
        parse_docx_bytes(_zip_bomb(), document_id="d", project_id="p")


def test_export_rejects_a_zip_bomb():
    with pytest.raises(DocxTooLarge):
        export_rev5_docx(_zip_bomb(), {0: "replacement"})

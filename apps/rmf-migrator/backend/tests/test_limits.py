"""Tests for the untrusted-.docx size guards (zip-bomb defense)."""

from __future__ import annotations

import io
import zipfile

import pytest
from docx import Document as DocxDocument

from rmf_migrator.common.limits import (
    MAX_COMPRESSION_RATIO,
    MAX_DOC_BYTES,
    MAX_DOCX_BYTES,
    MAX_UNCOMPRESSED_BYTES,
    DocxTooLarge,
    ObjectTooLarge,
    UnsupportedDocumentFormat,
    guard_docx_bytes,
    sniff_document_format,
    sniff_docx_problem,
)
from rmf_migrator.docx.export_docx import export_rev5_docx
from rmf_migrator.docx.parser import parse_docx_bytes
from tests.conftest import ole2_container, ole2_directory

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


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


def _lying_zip_bomb(actual_bytes: int = 80 * 1024 * 1024) -> bytes:
    """A zip bomb that under-declares its member's uncompressed size.

    Builds a real deflated member of ``actual_bytes``, then rewrites the
    uncompressed-size field in both the local file header (offset +22) and the
    central-directory header (offset +24) to a tiny value. A guard that trusts
    the declared sizes (``ZipInfo.file_size``) sees a small, low-ratio archive
    and lets it through.

    Note the exact failure mode downstream depends on the zip reader: CPython's
    ``zipfile`` caps member reads at the declared size and then fails the CRC
    check, while readers without that cap inflate the full ``actual_bytes``.
    The guard must reject the archive either way, without buffering it.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b" " * actual_bytes)
    raw = bytearray(buf.getvalue())

    forged = (4096).to_bytes(4, "little")

    lfh = raw.find(b"PK\x03\x04")
    if lfh != -1:
        raw[lfh + 22 : lfh + 26] = forged
    cdh = raw.find(b"PK\x01\x02")
    if cdh != -1:
        raw[cdh + 24 : cdh + 28] = forged

    return bytes(raw)


def test_real_document_passes_the_guard():
    guard_docx_bytes(_real_docx())  # must not raise


def test_zip_bomb_is_rejected_on_compression_ratio():
    data = _zip_bomb()
    assert len(data) < MAX_DOCX_BYTES  # small on the wire — that's the point
    with pytest.raises(DocxTooLarge):
        guard_docx_bytes(data)


def test_oversized_bytes_are_rejected():
    # Zip-signature-prefixed so the format sniff passes and the size ceiling
    # is what fires.
    with pytest.raises(DocxTooLarge):
        guard_docx_bytes(b"PK\x03\x04" + b"x" * (MAX_DOCX_BYTES + 1))


def _ole2_doc() -> bytes:
    """Bytes shaped like a legacy Word 97-2003 binary .doc (OLE2 container)."""
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512


_WML_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


def _fake_xlsx() -> bytes:
    """A valid OOXML zip that declares itself a spreadsheet, not a Word document."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types><Override PartName="/xl/workbook.xml" ContentType='
            '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>',
        )
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return buf.getvalue()


def _docx_like(main_part: str) -> bytes:
    """A minimal package declaring a WordprocessingML main document part."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            f'<Types><Override PartName="/{main_part}" ContentType="{_WML_MAIN}"/></Types>',
        )
        archive.writestr(main_part, "<document/>")
    return buf.getvalue()


def test_nonstandard_main_part_name_still_passes_the_guard():
    # python-docx finds the main part by relationship, not by name — Word
    # occasionally emits word/document2.xml, and those files parse fine. The
    # guard must accept what the parser accepts.
    guard_docx_bytes(_docx_like("word/document2.xml"))  # must not raise


def test_legacy_doc_bytes_are_rejected_as_unsupported_format():
    # A renamed .doc opens fine in Word, so the rejection has to say what it
    # actually is — not "too large".
    with pytest.raises(UnsupportedDocumentFormat):
        guard_docx_bytes(_ole2_doc())


def test_renamed_spreadsheet_is_rejected_as_unsupported_format():
    # Any zip passes a PK check; only a zip carrying word/document.xml is a
    # Word document. A renamed .xlsx must not reach python-docx and surface
    # as a raw ValueError.
    with pytest.raises(UnsupportedDocumentFormat):
        guard_docx_bytes(_fake_xlsx())


def test_plain_zip_is_rejected_as_unsupported_format():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("readme.txt", "hello")
    with pytest.raises(UnsupportedDocumentFormat):
        guard_docx_bytes(buf.getvalue())


def test_oversized_legacy_doc_reports_format_not_size():
    # The remedy for a huge legacy .doc is converting it, not shrinking it —
    # the format verdict must win over the size verdict.
    data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * MAX_DOCX_BYTES
    with pytest.raises(UnsupportedDocumentFormat):
        guard_docx_bytes(data)


def test_empty_bytes_are_rejected_as_unsupported_format():
    # A zero-byte object (interrupted PUT) is not a Word document either.
    with pytest.raises(UnsupportedDocumentFormat):
        guard_docx_bytes(b"")


def test_non_zip_bytes_are_rejected_as_wrong_format_not_size():
    """Garbage bytes are a *format* problem, not a size problem — reporting
    DocxTooLarge for them sends the operator chasing the wrong cause."""
    with pytest.raises(UnsupportedDocumentFormat):
        guard_docx_bytes(b"this is not a docx")


def test_legacy_doc_is_named_in_the_rejection():
    """A binary .doc renamed to .docx is the classic real-world failure; the
    error must say re-save as .docx, not talk about size limits."""
    ole2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
    with pytest.raises(UnsupportedDocumentFormat, match="legacy Word"):
        guard_docx_bytes(ole2)


def test_html_saved_as_docx_is_named_in_the_rejection():
    html = b"<!DOCTYPE html><html><body>Download page</body></html>"
    with pytest.raises(UnsupportedDocumentFormat, match="HTML"):
        guard_docx_bytes(html)


def test_pdf_renamed_to_docx_is_named_in_the_rejection():
    with pytest.raises(UnsupportedDocumentFormat, match="PDF"):
        guard_docx_bytes(b"%PDF-1.7 garbage")


def test_truncated_zip_is_reported_as_wrong_format():
    # Starts with the zip signature but is unreadable — zipfile's own
    # consistency check fires, and unreadable is a format verdict, not a
    # size verdict.
    with pytest.raises(UnsupportedDocumentFormat):
        guard_docx_bytes(b"PK\x03\x04" + b"\x00" * 32)


def test_parser_rejects_legacy_doc_bytes():
    with pytest.raises(UnsupportedDocumentFormat):
        parse_docx_bytes(_ole2_doc(), document_id="d", project_id="p")


def test_ratio_constant_is_sane_for_real_documents():
    data = _real_docx()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        total = sum(i.file_size for i in archive.infolist())
    assert total / len(data) < MAX_COMPRESSION_RATIO


def test_guard_rejects_a_zip_that_underdeclares_member_sizes():
    """The declared sizes in the zip's directory are attacker-controlled, so the
    guard must not trust them — it has to bound *actual* decompression."""
    data = _lying_zip_bomb()
    assert len(data) < MAX_DOCX_BYTES

    # Sanity: the forged archive looks tiny and low-ratio by its declared sizes,
    # which is exactly what a metadata-only guard would wave through.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        declared_total = sum(info.file_size for info in archive.infolist())
    assert declared_total < MAX_UNCOMPRESSED_BYTES
    assert declared_total / len(data) < MAX_COMPRESSION_RATIO

    # Rejection is what matters: the forged sizes either cross the actual-
    # decompression ceiling (DocxTooLarge) or trip zipfile's own consistency
    # checks (UnsupportedDocumentFormat) — both stop the bomb before it
    # reaches the parser.
    with pytest.raises((DocxTooLarge, UnsupportedDocumentFormat)):
        guard_docx_bytes(data)


def test_parser_rejects_a_lying_zip_bomb():
    with pytest.raises((DocxTooLarge, UnsupportedDocumentFormat)):
        parse_docx_bytes(_lying_zip_bomb(), document_id="d", project_id="p")


def test_parser_rejects_a_zip_bomb():
    with pytest.raises(DocxTooLarge):
        parse_docx_bytes(_zip_bomb(), document_id="d", project_id="p")


def test_export_rejects_a_zip_bomb():
    with pytest.raises(DocxTooLarge):
        export_rev5_docx(_zip_bomb(), {0: "replacement"})


def test_storage_download_enforces_the_streaming_byte_limit(deps):
    key = "projects/p/documents/d.docx"
    deps.store._s3.put_object(  # noqa: SLF001
        Bucket=deps.config.documents_bucket,
        Key=key,
        Body=b"1234",
    )

    with pytest.raises(ObjectTooLarge, match="exceeds"):
        deps.store.get_bytes(key, max_bytes=3)


def test_sniff_document_format_identifies_docx():
    assert sniff_document_format(_real_docx()) == "docx"


def test_sniff_document_format_identifies_legacy_doc():
    assert sniff_document_format(ole2_container("WordDocument", "1Table")) == "doc"


def test_sniff_document_format_finds_a_word_stream_past_the_first_directory_sector():
    # Word writes the root entry first, so `WordDocument` routinely lands in the
    # second directory sector; the chain has to be followed, not just sampled.
    container = ole2_container("Data", "1Table", "\x05SummaryInformation", "WordDocument")
    assert sniff_document_format(container) == "doc"


def _ole2_container_addressed_through_a_difat_sector() -> bytes:
    """A container whose directory sits past the reach of the header's DIFAT.

    The header names only the first 109 FAT sectors, which covers roughly the
    first 7 MB of a 512-byte-sector file; past that the FAT's own location has
    to be read from a DIFAT sector. MAX_DOC_BYTES allows 15 MB, so a real policy
    document with images reaches this layout — and a sniff that stopped at the
    header would reject it as "not a Word document".

    `WordDocument` is placed in the *second* directory sector, because that is
    what forces the FAT to be consulted at all.
    """
    sector = 512
    first_dir = 109 * (sector // 4)  # first sector no header DIFAT slot can address
    data = bytearray((first_dir + 3) * sector)
    data[0:8] = OLE2_MAGIC
    data[26:28] = (3).to_bytes(2, "little")  # major version
    data[28:30] = b"\xfe\xff"  # little-endian byte order
    data[30:32] = (9).to_bytes(2, "little")  # 512-byte sectors
    data[32:34] = (6).to_bytes(2, "little")  # 64-byte mini sectors
    data[48:52] = first_dir.to_bytes(4, "little")
    data[68:72] = (0).to_bytes(4, "little")  # DIFAT chain starts at sector 0
    data[72:76] = (1).to_bytes(4, "little")  # one DIFAT sector
    data[76:512] = b"\xff" * 436  # every header DIFAT slot free

    # Sector 0 is the DIFAT sector; its first slot names the 110th FAT sector.
    difat = 1 * sector
    data[difat : difat + sector] = b"\xff" * sector
    data[difat : difat + 4] = (1).to_bytes(4, "little")
    data[difat + sector - 4 : difat + sector] = (0xFFFFFFFE).to_bytes(4, "little")

    # Sector 1 is that FAT sector, holding the links for both directory sectors.
    fat = 2 * sector
    data[fat : fat + sector] = b"\xff" * sector
    data[fat : fat + 4] = (first_dir + 1).to_bytes(4, "little")
    data[fat + 4 : fat + 8] = (0xFFFFFFFE).to_bytes(4, "little")  # ENDOFCHAIN

    directory = ole2_directory("Data", "1Table", "\x05SummaryInformation", "WordDocument")
    at = (first_dir + 1) * sector
    data[at : at + len(directory)] = directory
    return bytes(data)


def test_sniff_document_format_reads_a_directory_addressed_through_a_difat_sector():
    assert sniff_document_format(_ole2_container_addressed_through_a_difat_sector()) == "doc"


def test_sniff_document_format_rejects_ole2_that_is_not_a_word_document():
    # The CFBF signature is the container's, not Word's: .xls, .ppt and .msi all
    # carry it, so only a WordDocument stream routes to the converter. This
    # stops honest non-Word files; a forged entry is the converter's
    # `--infilter` to catch, not this check's.
    assert sniff_document_format(ole2_container("Workbook")) is None
    assert sniff_document_format(ole2_container("PowerPoint Document")) is None
    # The name is compared by length as well as bytes, so a longer name that
    # merely starts with Word's does not match.
    assert sniff_document_format(ole2_container("WordDocumentSummary")) is None
    # ...and a name of exactly Word's length gets past the length check, so the
    # byte comparison is what has to reject it.
    assert sniff_document_format(ole2_container("Workbook0123")) is None
    # A storage (object type 1) named `WordDocument` is a directory node, not
    # the stream Word writes.
    assert sniff_document_format(ole2_container(("WordDocument", 1))) is None
    assert sniff_document_format(OLE2_MAGIC + b"rest of an OLE2 file") is None


def test_sniff_document_format_survives_an_illegal_cfbf_sector_size():
    # Sector shift is read straight out of untrusted bytes and then used as a
    # slice stride and a divisor. Anything but 9 or 12 has to be rejected at the
    # header, before the directory walk turns it into an IndexError (a 1-byte
    # "sector" indexed at offset 66) or a ZeroDivisionError (sector_size // 4
    # == 0). A malformed upload must classify as None, not crash the worker.
    for shift in (0, 1, 13):
        data = bytearray(4096)
        data[0:8] = OLE2_MAGIC
        data[30:32] = shift.to_bytes(2, "little")
        assert sniff_document_format(bytes(data)) is None


def test_sniff_document_format_rejects_impostors():
    assert sniff_document_format(b"%PDF-1.7 ...") is None
    assert sniff_document_format(b"<!doctype html><html></html>") is None
    assert sniff_document_format(b"") is None


def test_doc_ceiling_is_tighter_than_docx():
    assert MAX_DOC_BYTES < MAX_DOCX_BYTES


def test_sniff_docx_problem_still_names_a_renamed_legacy_doc():
    problem = sniff_docx_problem(OLE2_MAGIC + b"rest")
    assert problem is not None
    assert "legacy Word .doc" in problem


def test_sniff_docx_problem_never_sends_the_user_to_word():
    """These bytes were refused sight unseen — the shape a hostile document
    takes — so the OLE2 branch must not bless opening them in a local Word
    install. Mirrors test_doc_convert.py's assertion for
    CONVERSION_DISABLED_MESSAGE and client.test.ts's for sniffDocxProblem.
    """
    problem = sniff_docx_problem(OLE2_MAGIC + b"rest")
    assert problem is not None
    assert "Save As" not in problem
    assert "open it in Word" not in problem
    # The safe alternative the user manual §4.1 already names.
    assert "re-save" in problem

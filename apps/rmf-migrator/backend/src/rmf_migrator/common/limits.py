"""Size guards for untrusted document bytes.

A .docx is a zip of XML. python-docx decompresses every member into memory with
no ceiling, so a small upload can expand to hundreds of megabytes and OOM the
parse/export worker — cheap to send, expensive to absorb, and amplified by SQS
retries. The upload POST policy's content-length-range bounds the raw upload
size at S3, but the decompressed size is only knowable here — these checks are
what stands between an uploaded blob and the worker's memory.

The limits are deliberately generous: a real policy document, even a long one
with embedded images, lands far below them.
"""

from __future__ import annotations

import io
import zipfile

# Largest .docx we accept, compressed. Enforced on the S3 download.
MAX_DOCX_BYTES = 25 * 1024 * 1024  # 25 MB

# Largest legacy binary .doc we accept for conversion. Tighter than the .docx
# ceiling: OLE2 is uncompressed, so 15 MB of .doc is far more content than
# 15 MB of zipped XML, and the converter is the newest attack surface here.
MAX_DOC_BYTES = 15 * 1024 * 1024  # 15 MB

# Every object the .doc conversion round-trip may touch lives under this prefix.
# It is here, in the module both ends already import, because the two ends are
# in different packages: the caller stages its keys under it
# (doc_convert/lambda_backend.py) and the converter refuses anything outside it
# (converter_lambda/handler.py). Spelled out twice they would drift, and the
# drift fails closed as "refusing a key outside the scratch prefix" — a rename
# that reads like an attack. The trailing slash is part of it: without one,
# `convert-scratch-evil/...` satisfies a startswith check.
CONVERT_SCRATCH_PREFIX = "convert-scratch/"

# Largest total size once every zip member is decompressed.
MAX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024  # 300 MB

# A legitimate Office file's XML compresses well, but not absurdly. Anything
# past this ratio is a bomb, not a document.
MAX_COMPRESSION_RATIO = 200

# Bound extracted policy prose for synchronous Lambda/API responses.
MAX_PARSED_TEXT_BYTES = 4 * 1024 * 1024
MAX_SECTION_TEXT_BYTES = 300 * 1024
MAX_SECTION_COUNT = 5000
MAX_HEADING_CHARS = 1000

# Human-authored API input ceilings. Drafts share a DynamoDB item with the
# generated proposal, so the edit is byte-bounded below DynamoDB's 400 KiB cap.
MAX_DRAFT_TEXT_BYTES = 256 * 1024
MAX_CHAT_MESSAGE_CHARS = 12_000
MAX_CHAT_TOTAL_CHARS = 80_000


class ObjectTooLarge(ValueError):
    """Raised when a stored object exceeds the size we are willing to download."""


class DocxTooLarge(ValueError):
    """Raised when .docx bytes exceed a size or decompression-ratio limit."""


class UnsupportedDocumentFormat(ValueError):
    """Raised when the bytes are not a Word .docx at all (wrong format).

    Kept distinct from DocxTooLarge because the operator remedy is different:
    a renamed legacy .doc, an HTML download page, or a PDF needs re-saving as
    .docx, not shrinking. The class name is what gets recorded and logged
    (error types only, never content); the message names the specific
    impostor when the bytes make it identifiable.
    """


class ConversionMissing(ValueError):
    """Raised when a legacy .doc document has no converted .docx to read.

    Named for the same reason as UnsupportedDocumentFormat: the worker records
    ``type(exc).__name__`` as the operator-facing error, and a bare ValueError
    here is indistinguishable from any other failure in the parse stage. A
    named type also lets callers catch this case without swallowing unrelated
    ValueErrors.
    """


_ZIP_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Compound File Binary Format (MS-CFB) internals, read only as far as proving
# the container is Word's. Sizes and sentinel values are fixed by the format.
_CFBF_HEADER_BYTES = 512
_CFBF_DIR_ENTRY_BYTES = 128
_CFBF_STREAM_TYPE = 2
_CFBF_MAX_SECTOR = 0xFFFFFFFA  # above this a sector number is a sentinel
_CFBF_MAX_DIR_SECTORS = 1024  # bound a directory chain that loops or lies
_CFBF_MAX_DIFAT_SECTORS = 64
_WORD_STREAM_NAME = "WordDocument".encode("utf-16-le")


def _cfbf_sector(data: bytes, index: int, sector_size: int) -> bytes | None:
    """Sector `index`, or None when the file is shorter than it claims to be."""
    start = (index + 1) * sector_size  # the header occupies the first sector
    end = start + sector_size
    return data[start:end] if end <= len(data) else None


def _cfbf_fat_sectors(data: bytes, sector_size: int) -> list[int]:
    """Where the FAT lives: the header's 109 DIFAT slots, then the DIFAT chain."""
    sectors = [int.from_bytes(data[76 + i * 4 : 80 + i * 4], "little") for i in range(109)]
    index = int.from_bytes(data[68:72], "little")
    for _ in range(_CFBF_MAX_DIFAT_SECTORS):
        if index > _CFBF_MAX_SECTOR:
            break
        chunk = _cfbf_sector(data, index, sector_size)
        if chunk is None:
            break
        sectors += [
            int.from_bytes(chunk[at : at + 4], "little") for at in range(0, sector_size - 4, 4)
        ]
        index = int.from_bytes(chunk[-4:], "little")
    return sectors


def _cfbf_next_sector(
    data: bytes, sector: int, sector_size: int, fat_sectors: list[int]
) -> int | None:
    """Follow one FAT link; None when the FAT entry cannot be read."""
    index, offset = divmod(sector, sector_size // 4)
    if index >= len(fat_sectors):
        return None
    fat = _cfbf_sector(data, fat_sectors[index], sector_size)
    if fat is None:
        return None
    return int.from_bytes(fat[offset * 4 : offset * 4 + 4], "little")


def _has_word_document_stream(data: bytes) -> bool:
    """Whether a CFBF container's directory holds Word's `WordDocument` stream.

    The OLE2 signature marks the container, not the application: .xls, .ppt,
    .msi and .msg are byte-identical for those eight bytes. Routing on the
    signature alone would hand an Excel workbook to the .doc converter, which
    dispatches on content, so the directory chain is walked for the entry that
    actually says Word.

    This is a routing filter, not a containment boundary. The directory is
    attacker-supplied like the rest of the file: appending one forged entry to
    a real .xls satisfies this check. What it buys is that honest non-Word
    containers never reach the converter, and that reaching it at all requires
    forging structure rather than flipping eight bytes. Confining LibreOffice
    to the Word import filter must be enforced at the converter, by a pinned
    `--infilter` in its argv — do not read this check as sufficient for that.
    That flag was verified against the real image on LibreOffice 7.4.7.2; the
    evidence, and the conditions that would invalidate it, are in the plan's
    Gate 1 section (docs/superpowers/plans/2026-08-08-doc-conversion.md).

    Every walk is bounded and every read is range-checked: the sector numbers
    come from the same untrusted file.
    """
    if len(data) < _CFBF_HEADER_BYTES:
        return False
    sector_shift = int.from_bytes(data[30:32], "little")
    if sector_shift not in (9, 12):  # 512- or 4096-byte sectors; nothing else is legal
        return False
    sector_size = 1 << sector_shift

    fat_sectors = _cfbf_fat_sectors(data, sector_size)
    sector = int.from_bytes(data[48:52], "little")
    for _ in range(_CFBF_MAX_DIR_SECTORS):
        if sector > _CFBF_MAX_SECTOR:
            return False
        chunk = _cfbf_sector(data, sector, sector_size)
        if chunk is None:
            return False
        for at in range(0, sector_size, _CFBF_DIR_ENTRY_BYTES):
            entry = chunk[at : at + _CFBF_DIR_ENTRY_BYTES]
            # Name length counts the UTF-16 terminator, so an exact match on
            # both rules out a longer name that merely starts with ours.
            if (
                entry[66] == _CFBF_STREAM_TYPE
                and int.from_bytes(entry[64:66], "little") == len(_WORD_STREAM_NAME) + 2
                and entry[: len(_WORD_STREAM_NAME)] == _WORD_STREAM_NAME
            ):
                return True
        following = _cfbf_next_sector(data, sector, sector_size, fat_sectors)
        if following is None:
            return False
        sector = following
    return False


def sniff_document_format(data: bytes) -> str | None:
    """Classify Word document bytes: "docx", "doc", or None if neither.

    Extension is not evidence — a renamed file lies about its format, so the
    routing decision is made from content alone. "doc" means a Word 97-2003
    document specifically, not merely an OLE2 container; see
    `_has_word_document_stream` for why the distinction is load-bearing.
    """
    if data[:4] == _ZIP_MAGIC:
        return "docx"
    if data[:8] == _OLE2_MAGIC and _has_word_document_stream(data):
        return "doc"
    return None


def sniff_docx_problem(data: bytes) -> str | None:
    """Identify bytes that cannot be a .docx; return a human message or None.

    A .docx is a zip, so anything not starting with the zip local-file-header
    magic is wrong before decompression is even attempted. The common
    real-world impostors get named specifically — a renamed legacy .doc still
    opens in Word (Word sniffs content, not extensions), so these uploads
    look fine to the person who made them.
    """
    if not data:
        return "file is empty"
    if sniff_document_format(data) == "docx":
        return None
    if data[:8] == _OLE2_MAGIC:
        # Deliberately broader than the "doc" classification above: this is a
        # hint for a human who picked the wrong file, not a routing decision,
        # and a legacy Word .doc is by far the likeliest OLE2 file to land here.
        #
        # Never send the holder of these bytes to open them in Word: they were
        # refused sight unseen, which is the shape a hostile document takes,
        # and "open it locally" hands that container the macro surface,
        # network access and credentials the converter sandbox exists to deny.
        # Name a remedy that keeps the bytes out of a local Word install —
        # the same doctrine CONVERSION_DISABLED_MESSAGE and the frontend's
        # sniffDocxProblem already follow (doc_convert/rejecting.py,
        # frontend/src/api/client.ts).
        return (
            "this is a legacy Word .doc renamed to .docx — ask the document's "
            "author to re-save a .docx from their own copy, or upload it under "
            "its true .doc name so this deployment can convert it in its sandbox"
        )
    head = data[:512].lstrip()
    if head[:1] == b"<" or head[:15].lower().startswith(b"<!doctype html"):
        return "this is an HTML page saved with a .docx extension, not a Word document"
    if data[:5] == b"%PDF-":
        return "this is a PDF renamed to .docx, not a Word document"
    return "document is not a readable .docx archive"


# python-docx's own acceptance test (docx.api.Document): the package must
# declare a WordprocessingML main document part in [Content_Types].xml. The
# part's NAME is free — the library resolves it through the relationship
# graph, and real Word files occasionally carry word/document2.xml — so the
# guard matches the declaration, never a filename.
_CONTENT_TYPES_MEMBER = "[Content_Types].xml"
_WML_MAIN_CONTENT_TYPE = (
    b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)


class ParsedDocumentTooLarge(ValueError):
    """Raised when extracted policy text cannot safely traverse the application."""


# Members are decompressed in bounded chunks so the guard's own memory use stays
# flat no matter how large the archive claims — or actually turns out — to be.
_DECOMPRESS_CHUNK = 1024 * 1024  # 1 MB


def guard_docx_bytes(data: bytes) -> None:
    """Reject .docx bytes that would decompress to an unreasonable size.

    The zip central directory's declared member sizes are attacker-controlled: a
    crafted archive can under-report them so a metadata-only check waves it
    through, then decompress to gigabytes and OOM the worker. So this never
    trusts the declared sizes. It streams each member through a bounded buffer,
    tracking the *actual* decompressed total, and aborts the instant that total
    crosses the ceiling — before the bytes can accumulate in memory.
    """
    # Format first, size second: the remedies differ, and telling someone to
    # shrink a legacy .doc sends them down the wrong path entirely.
    problem = sniff_docx_problem(data)
    if problem is not None:
        raise UnsupportedDocumentFormat(problem)

    if len(data) > MAX_DOCX_BYTES:
        raise DocxTooLarge(f"document exceeds {MAX_DOCX_BYTES} bytes")

    # Two independent ceilings, whichever is tighter: an absolute cap and a
    # compression-ratio cap relative to the bytes on the wire (data is
    # non-empty here — the sniff guarantees a zip signature).
    ceiling = min(MAX_UNCOMPRESSED_BYTES, MAX_COMPRESSION_RATIO * len(data))

    # Any zip passes the sniff; only a package declaring a Word main
    # document is a .docx. The declaration is collected during the same
    # bounded walk (it is attacker-controlled bytes like everything else), so
    # a renamed spreadsheet or plain archive is named for what it is instead
    # of dying inside python-docx as a bare ValueError.
    content_types = bytearray()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                capture = info.filename == _CONTENT_TYPES_MEMBER
                with archive.open(info) as member:
                    while True:
                        chunk = member.read(_DECOMPRESS_CHUNK)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > ceiling:
                            raise DocxTooLarge(
                                "document decompresses past the allowed size/ratio limit"
                            )
                        if capture:
                            content_types.extend(chunk)
    except zipfile.BadZipFile as exc:
        # Magic bytes said zip, but the archive is truncated or corrupt.
        raise UnsupportedDocumentFormat("document is not a readable .docx archive") from exc

    if _WML_MAIN_CONTENT_TYPE not in bytes(content_types):
        raise UnsupportedDocumentFormat("zip package without a Word main document declaration")


def guard_parsed_sections(sections: list[object]) -> None:
    """Bound extracted content after decompression and XML parsing."""
    if len(sections) > MAX_SECTION_COUNT:
        raise ParsedDocumentTooLarge(f"document contains more than {MAX_SECTION_COUNT} sections")

    total = 0
    for section in sections:
        heading = str(getattr(section, "heading", ""))
        text = str(getattr(section, "text", ""))
        if len(heading) > MAX_HEADING_CHARS:
            raise ParsedDocumentTooLarge(f"section heading exceeds {MAX_HEADING_CHARS} characters")
        total += len(heading.encode("utf-8")) + len(text.encode("utf-8"))
        if total > MAX_PARSED_TEXT_BYTES:
            raise ParsedDocumentTooLarge(
                f"parsed policy text exceeds {MAX_PARSED_TEXT_BYTES} UTF-8 bytes"
            )

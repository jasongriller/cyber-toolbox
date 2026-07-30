"""Size guards for untrusted document bytes.

A .docx is a zip of XML. python-docx decompresses every member into memory with
no ceiling, so a small upload can expand to hundreds of megabytes and OOM the
parse/export worker — cheap to send, expensive to absorb, and amplified by SQS
retries. Nothing constrains the size of a presigned PUT either, so these checks
are the only thing standing between an uploaded blob and the worker's memory.

The limits are deliberately generous: a real policy document, even a long one
with embedded images, lands far below them.
"""

from __future__ import annotations

import io
import zipfile

# Largest .docx we accept, compressed. Enforced on the S3 download.
MAX_DOCX_BYTES = 25 * 1024 * 1024  # 25 MB

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
    """Raised when uploaded bytes are not an OOXML .docx container at all.

    Kept distinct from DocxTooLarge because the operator remedy is different:
    a legacy binary .doc (or arbitrary non-zip bytes) needs re-saving as
    .docx, not shrinking. The class name is the only thing recorded and
    logged (error types only, never content), so it has to say what happened.
    """


# Magic numbers, checked before any zip handling. A renamed legacy .doc still
# opens in Word (Word sniffs content, not extensions), so these uploads look
# fine to the person who made them.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # Word 97-2003 .doc container
_ZIP_MAGIC = b"PK"

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
    if data.startswith(_OLE2_MAGIC):
        raise UnsupportedDocumentFormat(
            "legacy binary .doc container; renaming does not convert it"
        )
    if not data.startswith(_ZIP_MAGIC):
        raise UnsupportedDocumentFormat("not an OOXML .docx container")

    if len(data) > MAX_DOCX_BYTES:
        raise DocxTooLarge(f"document exceeds {MAX_DOCX_BYTES} bytes")

    # Two independent ceilings, whichever is tighter: an absolute cap and a
    # compression-ratio cap relative to the bytes on the wire (data is
    # non-empty here — the PK check guarantees at least two bytes).
    ceiling = min(MAX_UNCOMPRESSED_BYTES, MAX_COMPRESSION_RATIO * len(data))

    # Any zip passes the magic check; only a package declaring a Word main
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
        raise DocxTooLarge("document is not a readable .docx archive") from exc

    if _WML_MAIN_CONTENT_TYPE not in bytes(content_types):
        raise UnsupportedDocumentFormat(
            "zip package without a Word main document declaration"
        )


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

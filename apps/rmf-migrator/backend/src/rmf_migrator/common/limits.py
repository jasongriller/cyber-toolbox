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
    if len(data) > MAX_DOCX_BYTES:
        raise DocxTooLarge(f"document exceeds {MAX_DOCX_BYTES} bytes")

    # Two independent ceilings, whichever is tighter: an absolute cap and a
    # compression-ratio cap relative to the bytes on the wire.
    ceiling = MAX_UNCOMPRESSED_BYTES
    if data:
        ceiling = min(ceiling, MAX_COMPRESSION_RATIO * len(data))

    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
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
    except zipfile.BadZipFile as exc:
        raise DocxTooLarge("document is not a readable .docx archive") from exc


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

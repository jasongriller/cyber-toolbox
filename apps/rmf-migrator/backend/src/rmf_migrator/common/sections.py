"""Helpers for section bodies externalized from DynamoDB to encrypted S3."""

from __future__ import annotations

from rmf_migrator.common.limits import MAX_PARSED_TEXT_BYTES, MAX_SECTION_TEXT_BYTES
from rmf_migrator.common.models import Section
from rmf_migrator.common.storage import DocumentStore, build_section_text_key


def externalize_large_section_texts(sections: list[Section], store: DocumentStore) -> list[Section]:
    """Move large bodies to S3 while preserving lightweight section metadata."""
    for section in sections:
        encoded = section.text.encode("utf-8")
        if len(encoded) <= MAX_SECTION_TEXT_BYTES:
            continue
        key = build_section_text_key(section.project_id, section.document_id, section.section_id)
        store.put_bytes(key, encoded, content_type="text/plain; charset=utf-8")
        section.text_s3_key = key
        section.text = ""
    return sections


def hydrate_section_texts(sections: list[Section], store: DocumentStore) -> list[Section]:
    """Load externalized bodies for processing or API responses."""
    for section in sections:
        if section.text_s3_key and not section.text:
            section.text = store.get_bytes(
                section.text_s3_key, max_bytes=MAX_PARSED_TEXT_BYTES
            ).decode("utf-8")
    return sections

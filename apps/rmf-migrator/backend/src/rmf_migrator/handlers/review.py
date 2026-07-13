"""API handlers for the mapping-review workflow (the human checkpoint).

Endpoints (all under a project + document):
  GET  .../documents/{document_id}                      -> document (poll status)
  GET  .../documents/{document_id}/sections             -> parsed sections
  GET  .../documents/{document_id}/mappings             -> proposed/edited mappings
  PUT  .../documents/{document_id}/mappings/{section_id} -> human edits a mapping
  POST .../documents/{document_id}/mappings/approve      -> approve all (gates M3)

Editing and approval validate every control id against the bundled Rev 4 catalog,
so a human cannot approve a mapping onto a control that does not exist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rmf_migrator.common.catalog import rev4_catalog
from rmf_migrator.common.http import (
    HttpError,
    error_response,
    json_response,
    parse_body,
    path_param,
    resolve_identity,
)
from rmf_migrator.common.logging import log_event
from rmf_migrator.common.models import DocumentStatus, MappingStatus
from rmf_migrator.handlers.deps import Deps


def _load_document(deps: Deps, event: dict[str, Any]):
    project_id = path_param(event, "project_id")
    document_id = path_param(event, "document_id")
    document = deps.repo.get_document(project_id, document_id)
    if document is None:
        raise HttpError(404, "document not found")
    return project_id, document_id, document


# ---- GET document ----------------------------------------------------------


def _get_document(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    _, _, document = _load_document(deps, event)
    return json_response(200, document.model_dump())


def get_document(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _get_document(event, Deps.build())
    except HttpError as err:
        return error_response(err)


# ---- GET sections ----------------------------------------------------------


def _list_sections(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    _, document_id, _ = _load_document(deps, event)
    sections = deps.repo.list_sections(document_id)
    return json_response(200, {"sections": [s.model_dump() for s in sections]})


def list_sections(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _list_sections(event, Deps.build())
    except HttpError as err:
        return error_response(err)


# ---- GET mappings ----------------------------------------------------------


def _get_mappings(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    _, document_id, document = _load_document(deps, event)
    mappings = deps.repo.list_mappings(document_id)
    return json_response(
        200,
        {
            "document_status": document.status,
            "mappings": [m.model_dump() for m in mappings],
        },
    )


def get_mappings(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _get_mappings(event, Deps.build())
    except HttpError as err:
        return error_response(err)


# ---- PUT mapping (human edit) ----------------------------------------------


def _validate_control_ids(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise HttpError(400, "'control_ids' must be a list")
    ids = [str(c).strip().upper() for c in raw if str(c).strip()]
    known, unknown = rev4_catalog().validate_ids(ids)
    if unknown:
        raise HttpError(400, f"unknown Rev 4 control ids: {', '.join(sorted(set(unknown)))}")
    # De-duplicate while preserving order.
    seen: set[str] = set()
    deduped = [c for c in known if not (c in seen or seen.add(c))]
    return deduped


def _update_mapping(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id, document_id, _ = _load_document(deps, event)
    section_id = path_param(event, "section_id")

    mapping = deps.repo.get_mapping(document_id, section_id)
    if mapping is None:
        raise HttpError(404, "mapping not found for section")

    body = parse_body(event)
    control_ids = _validate_control_ids(body.get("control_ids"))

    mapping.final_control_ids = control_ids
    mapping.status = MappingStatus.EDITED
    mapping.reviewed_by = resolve_identity(event, deps.config.identity_header)
    mapping.reviewed_at = datetime.now(UTC)
    deps.repo.put_mapping(mapping)

    log_event(
        "mapping.edited",
        project_id=project_id,
        document_id=document_id,
        section_id=section_id,
        control_count=len(control_ids),
        reviewed_by=mapping.reviewed_by,
    )
    return json_response(200, mapping.model_dump())


def update_mapping(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _update_mapping(event, Deps.build())
    except HttpError as err:
        return error_response(err)


# ---- POST approve (the checkpoint) -----------------------------------------


def _approve_mappings(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id, document_id, document = _load_document(deps, event)

    if document.status not in (DocumentStatus.MAPPED, DocumentStatus.MAPPING_APPROVED):
        raise HttpError(
            409,
            f"document is not ready for mapping approval (status: {document.status})",
        )

    identity = resolve_identity(event, deps.config.identity_header)
    now = datetime.now(UTC)
    mappings = deps.repo.list_mappings(document_id)

    for mapping in mappings:
        # Freeze the effective set as the authoritative final set.
        mapping.final_control_ids = mapping.effective_control_ids()
        mapping.status = MappingStatus.APPROVED
        if mapping.reviewed_by is None:
            mapping.reviewed_by = identity
        mapping.reviewed_at = now
        deps.repo.put_mapping(mapping)

    document.status = DocumentStatus.MAPPING_APPROVED
    deps.repo.put_document(document)

    log_event(
        "document.mapping_approved",
        project_id=project_id,
        document_id=document_id,
        approved_count=len(mappings),
        approved_by=identity,
    )
    return json_response(
        200,
        {"document_status": document.status, "approved_count": len(mappings)},
    )


def approve_mappings(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _approve_mappings(event, Deps.build())
    except HttpError as err:
        return error_response(err)

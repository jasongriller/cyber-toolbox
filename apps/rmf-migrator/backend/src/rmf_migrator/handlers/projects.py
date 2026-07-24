"""API handlers for browsing projects and their documents.

  GET .../projects                        -> every project in this deployment
  GET .../projects/{project_id}/documents -> that project's documents

These are the front door: without them a user has to already know a project id,
which is how the tool behaved before there was any navigation UI.
"""

from __future__ import annotations

from typing import Any

from rmf_migrator.common.http import (
    HttpError,
    error_response,
    json_response,
    parse_body,
    path_param,
    resolve_identity,
)
from rmf_migrator.common.logging import log_event
from rmf_migrator.handlers.deps import Deps

# ---- GET /projects ---------------------------------------------------------


def _list_projects(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    projects = deps.repo.list_projects()
    # Newest first — the thing you just created is the thing you want.
    projects.sort(key=lambda p: p.created_at, reverse=True)
    log_event("projects.listed", project_count=len(projects))
    return json_response(200, {"projects": [p.model_dump() for p in projects]})


def list_projects(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _list_projects(event, Deps.build())
    except HttpError as err:
        return error_response(err)


# ---- GET /projects/{project_id}/documents ----------------------------------


def _list_documents(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id = path_param(event, "project_id")
    if deps.repo.get_project(project_id) is None:
        raise HttpError(404, "project not found")

    documents = deps.repo.list_documents(project_id)
    documents.sort(key=lambda d: d.uploaded_at)
    return json_response(200, {"documents": [d.model_dump() for d in documents]})


def list_documents(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _list_documents(event, Deps.build())
    except HttpError as err:
        return error_response(err)


# ---- DELETE /projects/{project_id} -----------------------------------------


def _delete_project(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id = path_param(event, "project_id")
    project = deps.repo.get_project(project_id)
    if project is None:
        raise HttpError(404, "project not found")

    body = parse_body(event)
    if body.get("confirm_project_id") != project_id:
        raise HttpError(400, "confirm_project_id must exactly match the project being purged")

    deleted_objects = deps.store.delete_prefix(f"projects/{project_id}/")
    deleted_records = deps.repo.delete_project(project_id)
    deleted_by = resolve_identity(event, deps.config.identity_header)
    log_event(
        "project.purged",
        project_id=project_id,
        deleted_by=deleted_by,
        deleted_object_versions=deleted_objects,
        deleted_records=deleted_records,
    )
    return json_response(
        200,
        {
            "project_id": project_id,
            "deleted_object_versions": deleted_objects,
            "deleted_records": deleted_records,
        },
    )


def delete_project(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _delete_project(event, Deps.build())
    except HttpError as err:
        return error_response(err)

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
    resolve_groups,
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

# Members of this Cognito group may delete any project, not just their own.
_ADMIN_GROUP = "admins"


def _require_can_delete(event: dict[str, Any], deps: Deps, project) -> str:  # noqa: ANN001
    """Owner-gated destructive ops: reads stay open to the whole team, but a
    hard delete is bound to the project's creator or an "admins" group member.

    Projects whose created_by is "anonymous" (made before auth, or under
    auth_mode = "none", where identity is unverifiable anyway) cannot be bound
    to anyone, so they stay deletable by any caller — documented behavior, not
    a bypass: a spoofable identity would make any stricter check theater.
    """
    identity = resolve_identity(event, deps.config.identity_header)
    owner = project.created_by
    if owner in ("", "anonymous") or identity == owner:
        return identity
    if _ADMIN_GROUP in resolve_groups(event):
        return identity
    raise HttpError(
        403,
        f"only the project's creator ({owner!r}) or an {_ADMIN_GROUP!r} "
        "group member can delete this project",
    )


def _delete_project(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id = path_param(event, "project_id")
    project = deps.repo.get_project(project_id)
    if project is None:
        raise HttpError(404, "project not found")

    deleted_by = _require_can_delete(event, deps, project)

    body = parse_body(event)
    confirm_name = body.get("confirm_project_name")
    if not isinstance(confirm_name, str) or confirm_name.strip() != project.name:
        raise HttpError(
            400,
            "confirm_project_name must exactly match the project's name "
            f"({project.name!r}) to delete it",
        )

    deleted_objects = deps.store.delete_prefix(f"projects/{project_id}/")
    deleted_records = deps.repo.delete_project(project_id)
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

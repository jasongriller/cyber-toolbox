"""POST /projects — create a new A&A package project."""

from __future__ import annotations

from typing import Any

from rmf_migrator.common.http import (
    HttpError,
    error_response,
    json_response,
    parse_body,
    resolve_identity,
)
from rmf_migrator.common.logging import log_event
from rmf_migrator.common.models import Baseline, Project
from rmf_migrator.handlers.deps import Deps


def _create(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    body = parse_body(event)

    name = (body.get("name") or "").strip()
    if not name:
        raise HttpError(400, "project 'name' is required")
    if len(name) > 200:
        raise HttpError(400, "project 'name' too long")

    baseline_value = body.get("baseline", Baseline.GENERIC_800_53.value)
    try:
        baseline = Baseline(baseline_value)
    except ValueError as exc:
        valid = ", ".join(b.value for b in Baseline)
        raise HttpError(400, f"invalid baseline; choose one of: {valid}") from exc

    identity = resolve_identity(event, deps.config.identity_header)
    project = Project(name=name, baseline=baseline, created_by=identity)
    deps.repo.put_project(project)

    log_event(
        "project.created",
        project_id=project.project_id,
        baseline=baseline.value,
        created_by=identity,
    )
    return json_response(201, project.model_dump())


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _create(event, Deps.build())
    except HttpError as err:
        return error_response(err)

"""API handlers for package-level coverage and the conversion matrix.

  GET .../projects/{project_id}/coverage[?baseline=low|moderate|high]
  GET .../projects/{project_id}/conversion-matrix.csv

Both aggregate across every document in the project.
"""

from __future__ import annotations

from typing import Any

from rmf_migrator.common.http import (
    HttpError,
    error_response,
    json_response,
    path_param,
)
from rmf_migrator.common.logging import log_event
from rmf_migrator.handlers.deps import Deps
from rmf_migrator.services.conversion_matrix import Contribution, build_rows, to_csv
from rmf_migrator.services.coverage import build_coverage, resolve_baseline


def _require_project(deps: Deps, event: dict[str, Any]):
    project_id = path_param(event, "project_id")
    project = deps.repo.get_project(project_id)
    if project is None:
        raise HttpError(404, "project not found")
    return project_id, project


def _query(event: dict[str, Any], name: str) -> str | None:
    return (event.get("queryStringParameters") or {}).get(name)


# ---- GET coverage ----------------------------------------------------------


def _coverage(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id, project = _require_project(deps, event)

    override = _query(event, "baseline")
    try:
        baseline_name = resolve_baseline(project.baseline, override)
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc

    drafts = []
    for document in deps.repo.list_documents(project_id):
        drafts.extend(deps.repo.list_drafts(document.document_id))

    result = build_coverage(drafts, baseline_name=baseline_name)
    log_event(
        "coverage.computed",
        project_id=project_id,
        baseline=baseline_name or "none",
        covered_count=result["covered_count"],
    )
    return json_response(200, result)


def coverage(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _coverage(event, Deps.build())
    except HttpError as err:
        return error_response(err)


# ---- GET conversion matrix CSV ---------------------------------------------


def _conversion_matrix(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id, _ = _require_project(deps, event)

    contributions: list[Contribution] = []
    for document in deps.repo.list_documents(project_id):
        heading_by_section = {
            s.section_id: s.heading for s in deps.repo.list_sections(document.document_id)
        }
        for mapping in deps.repo.list_mappings(document.document_id):
            heading = heading_by_section.get(mapping.section_id, "")
            for rev4_id in mapping.effective_control_ids():
                contributions.append(
                    Contribution(rev4_id=rev4_id, filename=document.filename, heading=heading)
                )

    csv_text = to_csv(build_rows(contributions))
    log_event(
        "conversion_matrix.exported",
        project_id=project_id,
        contribution_count=len(contributions),
    )
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "text/csv",
            "Content-Disposition": f'attachment; filename="conversion-matrix-{project_id}.csv"',
        },
        "body": csv_text,
    }


def conversion_matrix(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _conversion_matrix(event, Deps.build())
    except HttpError as err:
        return error_response(err)

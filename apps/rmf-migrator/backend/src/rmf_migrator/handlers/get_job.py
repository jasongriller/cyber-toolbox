"""GET /projects/{project_id}/jobs/{job_id} — poll parse job status."""

from __future__ import annotations

from typing import Any

from rmf_migrator.common.http import (
    HttpError,
    error_response,
    json_response,
    path_param,
)
from rmf_migrator.handlers.deps import Deps


def _get_job(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id = path_param(event, "project_id")
    job_id = path_param(event, "job_id")

    job = deps.repo.get_job(project_id, job_id)
    if job is None:
        raise HttpError(404, "job not found")
    return json_response(200, job.model_dump())


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _get_job(event, Deps.build())
    except HttpError as err:
        return error_response(err)

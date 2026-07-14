"""Tests for the project/document listing handlers (the navigation front door)."""

from __future__ import annotations

import json

import pytest

from rmf_migrator.common.http import HttpError
from rmf_migrator.common.models import Baseline, Document, Project
from rmf_migrator.handlers.projects import _list_documents, _list_projects


def _event(path=None):
    return {"body": None, "pathParameters": path or {}, "headers": {}}


def test_list_projects_empty(deps):
    resp = _list_projects(_event(), deps)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["projects"] == []


def test_list_projects_returns_all(deps):
    deps.repo.put_project(Project(name="Alpha", baseline=Baseline.FIPS_199_LOW))
    deps.repo.put_project(Project(name="Beta"))

    resp = _list_projects(_event(), deps)
    projects = json.loads(resp["body"])["projects"]
    assert {p["name"] for p in projects} == {"Alpha", "Beta"}


def test_list_projects_newest_first(deps):
    from datetime import UTC, datetime

    old = Project(name="old", created_at=datetime(2020, 1, 1, tzinfo=UTC))
    new = Project(name="new", created_at=datetime(2030, 1, 1, tzinfo=UTC))
    deps.repo.put_project(old)
    deps.repo.put_project(new)

    projects = json.loads(_list_projects(_event(), deps)["body"])["projects"]
    assert [p["name"] for p in projects] == ["new", "old"]


def test_list_documents_for_project(deps):
    project = Project(name="Sys")
    deps.repo.put_project(project)
    pid = project.project_id
    deps.repo.put_document(Document(project_id=pid, filename="ac.docx", s3_key="k1"))
    deps.repo.put_document(Document(project_id=pid, filename="au.docx", s3_key="k2"))

    resp = _list_documents(_event(path={"project_id": pid}), deps)
    assert resp["statusCode"] == 200
    docs = json.loads(resp["body"])["documents"]
    assert {d["filename"] for d in docs} == {"ac.docx", "au.docx"}


def test_list_documents_scoped_to_its_project(deps):
    """A project's document list must not leak another project's documents."""
    a, b = Project(name="A"), Project(name="B")
    deps.repo.put_project(a)
    deps.repo.put_project(b)
    deps.repo.put_document(Document(project_id=a.project_id, filename="a.docx", s3_key="k"))
    deps.repo.put_document(Document(project_id=b.project_id, filename="b.docx", s3_key="k"))

    docs = json.loads(_list_documents(_event(path={"project_id": a.project_id}), deps)["body"])[
        "documents"
    ]
    assert [d["filename"] for d in docs] == ["a.docx"]


def test_list_documents_404_unknown_project(deps):
    with pytest.raises(HttpError) as exc:
        _list_documents(_event(path={"project_id": "proj_missing"}), deps)
    assert exc.value.status == 404

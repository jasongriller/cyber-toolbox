"""Tests for the project/document listing handlers (the navigation front door)."""

from __future__ import annotations

import json

import pytest

from rmf_migrator.common.http import HttpError
from rmf_migrator.common.models import Baseline, Document, ParseJob, Project, Section
from rmf_migrator.handlers.projects import _delete_project, _list_documents, _list_projects


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


def test_delete_project_rejects_wrong_name(deps):
    project = Project(name="Sys")
    deps.repo.put_project(project)

    with pytest.raises(HttpError) as exc:
        _delete_project(
            {
                "body": json.dumps({"confirm_project_name": "Not Sys"}),
                "pathParameters": {"project_id": project.project_id},
                "headers": {},
            },
            deps,
        )

    assert exc.value.status == 400
    assert deps.repo.get_project(project.project_id) is not None


def test_delete_project_rejects_case_mismatch(deps):
    project = Project(name="Sys")
    deps.repo.put_project(project)

    with pytest.raises(HttpError) as exc:
        _delete_project(
            {
                "body": json.dumps({"confirm_project_name": "sys"}),
                "pathParameters": {"project_id": project.project_id},
                "headers": {},
            },
            deps,
        )

    assert exc.value.status == 400
    assert deps.repo.get_project(project.project_id) is not None


def test_delete_project_rejects_missing_confirmation_field(deps):
    project = Project(name="Sys")
    deps.repo.put_project(project)

    with pytest.raises(HttpError) as exc:
        _delete_project(
            {
                "body": json.dumps({}),
                "pathParameters": {"project_id": project.project_id},
                "headers": {},
            },
            deps,
        )

    assert exc.value.status == 400
    assert deps.repo.get_project(project.project_id) is not None


def test_delete_project_accepts_whitespace_trimmed_name(deps):
    project = Project(name="Sys")
    deps.repo.put_project(project)

    response = _delete_project(
        {
            "body": json.dumps({"confirm_project_name": "  Sys  "}),
            "pathParameters": {"project_id": project.project_id},
            "headers": {},
        },
        deps,
    )

    assert response["statusCode"] == 200
    assert deps.repo.get_project(project.project_id) is None


def test_delete_project_purges_metadata_and_all_s3_versions(deps):
    project = Project(name="Sys")
    deps.repo.put_project(project)
    pid = project.project_id
    document = Document(
        project_id=pid,
        filename="ac.docx",
        s3_key=f"projects/{pid}/documents/ac.docx",
    )
    deps.repo.put_document(document)
    deps.repo.put_sections(
        [Section(document_id=document.document_id, project_id=pid, order=0, level=1)]
    )
    job = ParseJob(project_id=pid, document_id=document.document_id)
    deps.repo.put_job(job)

    s3 = deps.store._s3  # noqa: SLF001
    s3.put_bucket_versioning(
        Bucket=deps.config.documents_bucket,
        VersioningConfiguration={"Status": "Enabled"},
    )
    for body in (b"v1", b"v2"):
        s3.put_object(
            Bucket=deps.config.documents_bucket,
            Key=document.s3_key,
            Body=body,
        )
    s3.delete_object(Bucket=deps.config.documents_bucket, Key=document.s3_key)

    response = _delete_project(
        {
            "body": json.dumps({"confirm_project_name": "Sys"}),
            "pathParameters": {"project_id": pid},
            "headers": {"X-Remote-User": "reviewer"},
        },
        deps,
    )

    payload = json.loads(response["body"])
    assert payload["deleted_object_versions"] == 3
    assert deps.repo.get_project(pid) is None
    assert deps.repo.get_document(pid, document.document_id) is None
    assert deps.repo.list_sections(document.document_id) == []
    assert deps.repo.get_job(pid, job.job_id) is None
    versions = s3.list_object_versions(
        Bucket=deps.config.documents_bucket,
        Prefix=f"projects/{pid}/",
    )
    assert versions.get("Versions", []) == []
    assert versions.get("DeleteMarkers", []) == []


# ---- delete authorization (owner-gated destructive ops) --------------------


def _jwt_event(username, *, path=None, body=None, groups=None):
    claims = {"cognito:username": username}
    if groups is not None:
        claims["cognito:groups"] = groups
    return {
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "headers": {},
        "requestContext": {"authorizer": {"jwt": {"claims": claims}}},
    }


def _owned_project(deps, owner="alice"):
    project = Project(name="Sys", created_by=owner)
    deps.repo.put_project(project)
    return project


def test_delete_project_allows_the_owner(deps):
    project = _owned_project(deps, owner="alice")
    resp = _delete_project(
        _jwt_event(
            "alice",
            path={"project_id": project.project_id},
            body={"confirm_project_name": "Sys"},
        ),
        deps,
    )
    assert resp["statusCode"] == 200


def test_delete_project_403_for_a_non_owner(deps):
    project = _owned_project(deps, owner="alice")
    with pytest.raises(HttpError) as err:
        _delete_project(
            _jwt_event(
                "mallory",
                path={"project_id": project.project_id},
                body={"confirm_project_name": "Sys"},
            ),
            deps,
        )
    assert err.value.status == 403
    # Nothing was purged.
    assert deps.repo.get_project(project.project_id) is not None


def test_delete_project_allows_an_admins_group_member(deps):
    project = _owned_project(deps, owner="alice")
    resp = _delete_project(
        _jwt_event(
            "ops-admin",
            groups='["admins"]',  # HTTP API flattens the claim to a string
            path={"project_id": project.project_id},
            body={"confirm_project_name": "Sys"},
        ),
        deps,
    )
    assert resp["statusCode"] == 200


def test_delete_project_allows_bracket_space_group_claim(deps):
    project = _owned_project(deps, owner="alice")
    resp = _delete_project(
        _jwt_event(
            "ops-admin",
            groups="[admins users]",  # the other HTTP API serialization
            path={"project_id": project.project_id},
            body={"confirm_project_name": "Sys"},
        ),
        deps,
    )
    assert resp["statusCode"] == 200


def test_delete_project_keeps_working_for_anonymous_owned_projects(deps):
    """Projects created before auth (or in auth_mode none) carry
    created_by="anonymous"; the gate cannot bind them to anyone, so any
    authenticated caller may still delete them."""
    project = _owned_project(deps, owner="anonymous")
    resp = _delete_project(
        _jwt_event(
            "bob",
            path={"project_id": project.project_id},
            body={"confirm_project_name": "Sys"},
        ),
        deps,
    )
    assert resp["statusCode"] == 200

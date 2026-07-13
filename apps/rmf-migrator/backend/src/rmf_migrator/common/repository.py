"""DynamoDB single-table repository.

Single-table design keyed by ``PK``/``SK``:

    Project    PK=PROJECT#<pid>            SK=META
    Document   PK=PROJECT#<pid>            SK=DOC#<did>
    Section    PK=DOC#<did>               SK=SEC#<order padded>
    ParseJob   PK=PROJECT#<pid>            SK=JOB#<job_id>

This keeps a project's documents and jobs in one partition (cheap list), and a
document's sections in their own partition (cheap ordered scan). Later
milestones add mapping/decision items under the same keys.

All items are encrypted at rest by the table's KMS CMK (configured in Terraform);
no crypto happens here.
"""

from __future__ import annotations

from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from .models import Document, ParseJob, Project, Section


def _project_pk(project_id: str) -> str:
    return f"PROJECT#{project_id}"


def _doc_pk(document_id: str) -> str:
    return f"DOC#{document_id}"


class Repository:
    def __init__(self, table_name: str, *, dynamodb_resource: Any = None) -> None:
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)

    # ---- Project -----------------------------------------------------------

    def put_project(self, project: Project) -> None:
        item = _to_item(project)
        item |= {"PK": _project_pk(project.project_id), "SK": "META", "type": "project"}
        self._table.put_item(Item=item)

    def get_project(self, project_id: str) -> Project | None:
        resp = self._table.get_item(Key={"PK": _project_pk(project_id), "SK": "META"})
        item = resp.get("Item")
        return Project(**_strip_keys(item)) if item else None

    def list_projects(self) -> list[Project]:
        # Small-scale tool: a scan on the META rows is acceptable. A GSI can be
        # added if project counts grow large.
        resp = self._table.scan(
            FilterExpression=Key("SK").eq("META"),
        )
        return [Project(**_strip_keys(i)) for i in resp.get("Items", [])]

    def increment_document_count(self, project_id: str, delta: int = 1) -> None:
        self._table.update_item(
            Key={"PK": _project_pk(project_id), "SK": "META"},
            UpdateExpression="SET document_count = if_not_exists(document_count, :z) + :d",
            ExpressionAttributeValues={":d": delta, ":z": 0},
        )

    # ---- Document ----------------------------------------------------------

    def put_document(self, document: Document) -> None:
        item = _to_item(document)
        item |= {
            "PK": _project_pk(document.project_id),
            "SK": f"DOC#{document.document_id}",
            "type": "document",
        }
        self._table.put_item(Item=item)

    def get_document(self, project_id: str, document_id: str) -> Document | None:
        resp = self._table.get_item(Key={"PK": _project_pk(project_id), "SK": f"DOC#{document_id}"})
        item = resp.get("Item")
        return Document(**_strip_keys(item)) if item else None

    def list_documents(self, project_id: str) -> list[Document]:
        resp = self._table.query(
            KeyConditionExpression=Key("PK").eq(_project_pk(project_id))
            & Key("SK").begins_with("DOC#")
        )
        return [Document(**_strip_keys(i)) for i in resp.get("Items", [])]

    # ---- Section -----------------------------------------------------------

    def put_sections(self, sections: list[Section]) -> None:
        with self._table.batch_writer() as batch:
            for section in sections:
                item = _to_item(section)
                item |= {
                    "PK": _doc_pk(section.document_id),
                    "SK": f"SEC#{section.order:06d}",
                    "type": "section",
                }
                batch.put_item(Item=item)

    def list_sections(self, document_id: str) -> list[Section]:
        resp = self._table.query(
            KeyConditionExpression=Key("PK").eq(_doc_pk(document_id))
            & Key("SK").begins_with("SEC#")
        )
        return [Section(**_strip_keys(i)) for i in resp.get("Items", [])]

    # ---- ParseJob ----------------------------------------------------------

    def put_job(self, job: ParseJob) -> None:
        item = _to_item(job)
        item |= {
            "PK": _project_pk(job.project_id),
            "SK": f"JOB#{job.job_id}",
            "type": "job",
        }
        self._table.put_item(Item=item)

    def get_job(self, project_id: str, job_id: str) -> ParseJob | None:
        resp = self._table.get_item(Key={"PK": _project_pk(project_id), "SK": f"JOB#{job_id}"})
        item = resp.get("Item")
        return ParseJob(**_strip_keys(item)) if item else None


_KEY_ATTRS = {"PK", "SK", "type"}


def _to_item(model: Any) -> dict[str, Any]:
    """Serialize a pydantic model to a DynamoDB-safe dict (JSON round-trip)."""
    import json

    return json.loads(model.model_dump_json())


def _strip_keys(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in _KEY_ATTRS}

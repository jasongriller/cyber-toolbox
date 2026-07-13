"""DynamoDB single-table repository.

Single-table design keyed by ``PK``/``SK``:

    Project        PK=PROJECT#<pid>        SK=META
    Document       PK=PROJECT#<pid>        SK=DOC#<did>
    ParseJob       PK=PROJECT#<pid>        SK=JOB#<job_id>
    MappingJob     PK=PROJECT#<pid>        SK=MJOB#<job_id>
    DraftJob       PK=PROJECT#<pid>        SK=DJOB#<job_id>
    Section        PK=DOC#<did>            SK=SEC#<order padded>
    ControlMapping PK=DOC#<did>            SK=MAP#<section_id>
    Draft          PK=DOC#<did>            SK=DRAFT#<section_id>

This keeps a project's documents and jobs in one partition (cheap list), and a
document's sections and mappings in their own partition (cheap scan). Later
milestones add decision-log items under the same keys.

All items are encrypted at rest by the table's KMS CMK (configured in Terraform);
no crypto happens here.
"""

from __future__ import annotations

from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from .models import (
    ControlMapping,
    Document,
    Draft,
    DraftJob,
    MappingJob,
    ParseJob,
    Project,
    Section,
)


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

    # ---- MappingJob --------------------------------------------------------

    def put_mapping_job(self, job: MappingJob) -> None:
        item = _to_item(job)
        item |= {
            "PK": _project_pk(job.project_id),
            "SK": f"MJOB#{job.job_id}",
            "type": "mapping_job",
        }
        self._table.put_item(Item=item)

    def get_mapping_job(self, project_id: str, job_id: str) -> MappingJob | None:
        resp = self._table.get_item(Key={"PK": _project_pk(project_id), "SK": f"MJOB#{job_id}"})
        item = resp.get("Item")
        return MappingJob(**_strip_keys(item)) if item else None

    # ---- ControlMapping ----------------------------------------------------

    def put_mappings(self, mappings: list[ControlMapping]) -> None:
        with self._table.batch_writer() as batch:
            for mapping in mappings:
                item = _to_item(mapping)
                item |= {
                    "PK": _doc_pk(mapping.document_id),
                    "SK": f"MAP#{mapping.section_id}",
                    "type": "mapping",
                }
                batch.put_item(Item=item)

    def put_mapping(self, mapping: ControlMapping) -> None:
        item = _to_item(mapping)
        item |= {
            "PK": _doc_pk(mapping.document_id),
            "SK": f"MAP#{mapping.section_id}",
            "type": "mapping",
        }
        self._table.put_item(Item=item)

    def get_mapping(self, document_id: str, section_id: str) -> ControlMapping | None:
        resp = self._table.get_item(Key={"PK": _doc_pk(document_id), "SK": f"MAP#{section_id}"})
        item = resp.get("Item")
        return ControlMapping(**_strip_keys(item)) if item else None

    def list_mappings(self, document_id: str) -> list[ControlMapping]:
        resp = self._table.query(
            KeyConditionExpression=Key("PK").eq(_doc_pk(document_id))
            & Key("SK").begins_with("MAP#")
        )
        mappings = [ControlMapping(**_strip_keys(i)) for i in resp.get("Items", [])]
        mappings.sort(key=lambda m: m.order)
        return mappings

    # ---- DraftJob ----------------------------------------------------------

    def put_draft_job(self, job: DraftJob) -> None:
        item = _to_item(job)
        item |= {
            "PK": _project_pk(job.project_id),
            "SK": f"DJOB#{job.job_id}",
            "type": "draft_job",
        }
        self._table.put_item(Item=item)

    def get_draft_job(self, project_id: str, job_id: str) -> DraftJob | None:
        resp = self._table.get_item(Key={"PK": _project_pk(project_id), "SK": f"DJOB#{job_id}"})
        item = resp.get("Item")
        return DraftJob(**_strip_keys(item)) if item else None

    # ---- Draft -------------------------------------------------------------

    def put_drafts(self, drafts: list[Draft]) -> None:
        with self._table.batch_writer() as batch:
            for draft in drafts:
                item = _to_item(draft)
                item |= {
                    "PK": _doc_pk(draft.document_id),
                    "SK": f"DRAFT#{draft.section_id}",
                    "type": "draft",
                }
                batch.put_item(Item=item)

    def put_draft(self, draft: Draft) -> None:
        item = _to_item(draft)
        item |= {
            "PK": _doc_pk(draft.document_id),
            "SK": f"DRAFT#{draft.section_id}",
            "type": "draft",
        }
        self._table.put_item(Item=item)

    def get_draft(self, document_id: str, section_id: str) -> Draft | None:
        resp = self._table.get_item(Key={"PK": _doc_pk(document_id), "SK": f"DRAFT#{section_id}"})
        item = resp.get("Item")
        return Draft(**_strip_keys(item)) if item else None

    def list_drafts(self, document_id: str) -> list[Draft]:
        resp = self._table.query(
            KeyConditionExpression=Key("PK").eq(_doc_pk(document_id))
            & Key("SK").begins_with("DRAFT#")
        )
        drafts = [Draft(**_strip_keys(i)) for i in resp.get("Items", [])]
        drafts.sort(key=lambda d: d.order)
        return drafts


_KEY_ATTRS = {"PK", "SK", "type"}


def _to_item(model: Any) -> dict[str, Any]:
    """Serialize a pydantic model to a DynamoDB-safe dict.

    The JSON round-trip normalizes enums/datetimes to primitives; ``parse_float=
    Decimal`` converts floats (e.g. mapping confidence) to Decimal, which the
    DynamoDB resource client requires — it rejects native Python floats.
    """
    import json
    from decimal import Decimal

    return json.loads(model.model_dump_json(), parse_float=Decimal)


def _strip_keys(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in _KEY_ATTRS}

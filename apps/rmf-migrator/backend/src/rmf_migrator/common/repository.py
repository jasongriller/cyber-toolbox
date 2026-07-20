"""DynamoDB single-table repository.

Single-table design keyed by ``PK``/``SK``:

    Project        PK=PROJECT#<pid>        SK=META
    Document       PK=PROJECT#<pid>        SK=DOC#<did>
    ParseJob       PK=PROJECT#<pid>        SK=JOB#<job_id>
    MappingJob     PK=PROJECT#<pid>        SK=MJOB#<job_id>
    DraftJob       PK=PROJECT#<pid>        SK=DJOB#<job_id>
    ExportJob      PK=PROJECT#<pid>        SK=XJOB#<job_id>
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

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .models import (
    ControlMapping,
    Document,
    DocumentStatus,
    Draft,
    DraftJob,
    ExportJob,
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

    def _query_all(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Consume every DynamoDB query page."""
        items: list[dict[str, Any]] = []
        request = dict(kwargs)
        while True:
            response = self._table.query(**request)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            request["ExclusiveStartKey"] = last_key

    def _scan_all(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Consume every DynamoDB scan page."""
        items: list[dict[str, Any]] = []
        request = dict(kwargs)
        while True:
            response = self._table.scan(**request)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            request["ExclusiveStartKey"] = last_key

    def get_project(self, project_id: str) -> Project | None:
        resp = self._table.get_item(
            Key={"PK": _project_pk(project_id), "SK": "META"}, ConsistentRead=True
        )
        item = resp.get("Item")
        return Project(**_strip_keys(item)) if item else None

    def list_projects(self) -> list[Project]:
        # Small-scale tool: a scan on the META rows is acceptable. A GSI can be
        # added if project counts grow large.
        items = self._scan_all(FilterExpression=Key("SK").eq("META"), ConsistentRead=True)
        return [Project(**_strip_keys(i)) for i in items]

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

    def put_document_if_status(self, document: Document, expected: set[DocumentStatus]) -> bool:
        """Atomically replace a document only when its persisted state is expected."""
        if not expected:
            raise ValueError("expected statuses cannot be empty")
        item = _to_item(document)
        item |= {
            "PK": _project_pk(document.project_id),
            "SK": f"DOC#{document.document_id}",
            "type": "document",
        }
        values = {f":s{i}": status.value for i, status in enumerate(sorted(expected))}
        condition = f"#status IN ({', '.join(values)})"
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression=condition,
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=values,
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def get_document(self, project_id: str, document_id: str) -> Document | None:
        resp = self._table.get_item(
            Key={"PK": _project_pk(project_id), "SK": f"DOC#{document_id}"},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        return Document(**_strip_keys(item)) if item else None

    def list_documents(self, project_id: str) -> list[Document]:
        items = self._query_all(
            KeyConditionExpression=Key("PK").eq(_project_pk(project_id))
            & Key("SK").begins_with("DOC#"),
            ConsistentRead=True,
        )
        return [Document(**_strip_keys(i)) for i in items]

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
        items = self._query_all(
            KeyConditionExpression=Key("PK").eq(_doc_pk(document_id))
            & Key("SK").begins_with("SEC#"),
            ConsistentRead=True,
        )
        return [Section(**_strip_keys(i)) for i in items]

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
        resp = self._table.get_item(
            Key={"PK": _project_pk(project_id), "SK": f"JOB#{job_id}"},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        return ParseJob(**_strip_keys(item)) if item else None

    def claim_job(self, project_id: str, job_id: str) -> ParseJob | None:
        return self._claim_job(project_id, f"JOB#{job_id}", ParseJob)

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
        resp = self._table.get_item(
            Key={"PK": _project_pk(project_id), "SK": f"MJOB#{job_id}"},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        return MappingJob(**_strip_keys(item)) if item else None

    def claim_mapping_job(self, project_id: str, job_id: str) -> MappingJob | None:
        return self._claim_job(project_id, f"MJOB#{job_id}", MappingJob)

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
        resp = self._table.get_item(
            Key={"PK": _doc_pk(document_id), "SK": f"MAP#{section_id}"},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        return ControlMapping(**_strip_keys(item)) if item else None

    def list_mappings(self, document_id: str) -> list[ControlMapping]:
        items = self._query_all(
            KeyConditionExpression=Key("PK").eq(_doc_pk(document_id))
            & Key("SK").begins_with("MAP#"),
            ConsistentRead=True,
        )
        mappings = [ControlMapping(**_strip_keys(i)) for i in items]
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
        resp = self._table.get_item(
            Key={"PK": _project_pk(project_id), "SK": f"DJOB#{job_id}"},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        return DraftJob(**_strip_keys(item)) if item else None

    def claim_draft_job(self, project_id: str, job_id: str) -> DraftJob | None:
        return self._claim_job(project_id, f"DJOB#{job_id}", DraftJob)

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
        resp = self._table.get_item(
            Key={"PK": _doc_pk(document_id), "SK": f"DRAFT#{section_id}"},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        return Draft(**_strip_keys(item)) if item else None

    # ---- ExportJob ---------------------------------------------------------

    def put_export_job(self, job: ExportJob) -> None:
        item = _to_item(job)
        item |= {
            "PK": _project_pk(job.project_id),
            "SK": f"XJOB#{job.job_id}",
            "type": "export_job",
        }
        self._table.put_item(Item=item)

    def get_export_job(self, project_id: str, job_id: str) -> ExportJob | None:
        resp = self._table.get_item(
            Key={"PK": _project_pk(project_id), "SK": f"XJOB#{job_id}"},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        return ExportJob(**_strip_keys(item)) if item else None

    def claim_export_job(self, project_id: str, job_id: str) -> ExportJob | None:
        return self._claim_job(project_id, f"XJOB#{job_id}", ExportJob)

    def list_drafts(self, document_id: str) -> list[Draft]:
        items = self._query_all(
            KeyConditionExpression=Key("PK").eq(_doc_pk(document_id))
            & Key("SK").begins_with("DRAFT#"),
            ConsistentRead=True,
        )
        drafts = [Draft(**_strip_keys(i)) for i in items]
        drafts.sort(key=lambda d: d.order)
        return drafts

    def _claim_job(self, project_id: str, sort_key: str, model_type: Any) -> Any | None:
        """Claim a new/retry job, including a worker lease that has timed out."""
        now = datetime.now(UTC)
        stale_before = now - timedelta(minutes=15)
        try:
            response = self._table.update_item(
                Key={"PK": _project_pk(project_id), "SK": sort_key},
                UpdateExpression="SET #status = :running, #updated = :now REMOVE error_type",
                ConditionExpression=(
                    "attribute_exists(PK) AND ("
                    "#status = :pending OR #status = :failed OR "
                    "(#status = :running AND "
                    "(attribute_not_exists(#updated) OR #updated < :stale)))"
                ),
                ExpressionAttributeNames={"#status": "status", "#updated": "updated_at"},
                ExpressionAttributeValues={
                    ":running": "running",
                    ":pending": "pending",
                    ":failed": "failed",
                    ":now": now.isoformat(),
                    ":stale": stale_before.isoformat(),
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return None
            raise
        return model_type(**_strip_keys(response["Attributes"]))

    def delete_project(self, project_id: str) -> int:
        """Delete a project's metadata and every document-scoped partition."""
        documents = self.list_documents(project_id)
        deleted = sum(self._delete_partition(_doc_pk(d.document_id)) for d in documents)
        project_key = _project_pk(project_id)
        project_items = self._query_all(
            KeyConditionExpression=Key("PK").eq(project_key),
            ProjectionExpression="PK, SK",
            ConsistentRead=True,
        )
        non_meta = [item for item in project_items if item["SK"] != "META"]
        with self._table.batch_writer() as batch:
            for item in non_meta:
                batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
        deleted += len(non_meta)

        # Keep META until every child record has gone. If the process is
        # interrupted, the project remains discoverable and a retry can finish.
        if any(item["SK"] == "META" for item in project_items):
            self._table.delete_item(Key={"PK": project_key, "SK": "META"})
            deleted += 1
        return deleted

    def _delete_partition(self, partition_key: str) -> int:
        items = self._query_all(
            KeyConditionExpression=Key("PK").eq(partition_key),
            ProjectionExpression="PK, SK",
            ConsistentRead=True,
        )
        with self._table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
        return len(items)


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

"""Focused tests for DynamoDB paging helpers."""

from __future__ import annotations

from rmf_migrator.common.repository import Repository


class _PagedTable:
    def __init__(self) -> None:
        self.query_calls: list[dict] = []
        self.scan_calls: list[dict] = []

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        if len(self.query_calls) == 1:
            return {"Items": [{"id": 1}], "LastEvaluatedKey": {"PK": "next"}}
        return {"Items": [{"id": 2}]}

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        if len(self.scan_calls) == 1:
            return {"Items": [{"id": 1}], "LastEvaluatedKey": {"PK": "next"}}
        return {"Items": [{"id": 2}]}


class _Resource:
    def __init__(self, table: _PagedTable) -> None:
        self.table = table

    def Table(self, _name: str):  # noqa: N802 - mirrors boto3's API
        return self.table


def test_query_all_consumes_last_evaluated_key():
    table = _PagedTable()
    repo = Repository("table", dynamodb_resource=_Resource(table))

    assert repo._query_all(ConsistentRead=True) == [{"id": 1}, {"id": 2}]  # noqa: SLF001
    assert table.query_calls[1]["ExclusiveStartKey"] == {"PK": "next"}
    assert table.query_calls[1]["ConsistentRead"] is True


def test_scan_all_consumes_last_evaluated_key():
    table = _PagedTable()
    repo = Repository("table", dynamodb_resource=_Resource(table))

    assert repo._scan_all(ConsistentRead=True) == [{"id": 1}, {"id": 2}]  # noqa: SLF001
    assert table.scan_calls[1]["ExclusiveStartKey"] == {"PK": "next"}
    assert table.scan_calls[1]["ConsistentRead"] is True

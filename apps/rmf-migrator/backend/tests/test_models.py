"""Model invariants and DynamoDB round-tripping."""

from __future__ import annotations

from rmf_migrator.common.models import (
    Baseline,
    Document,
    DocumentStatus,
    JobStatus,
    ParseJob,
    Project,
    Section,
)
from rmf_migrator.common.repository import _strip_keys, _to_item


def test_ids_are_prefixed_and_unique():
    p1, p2 = Project(name="a"), Project(name="b")
    assert p1.project_id.startswith("proj_")
    assert p1.project_id != p2.project_id
    assert Document(project_id="p", filename="f", s3_key="k").document_id.startswith("doc_")
    assert Section(document_id="d", project_id="p", order=0, level=1).section_id.startswith("sec_")
    assert ParseJob(project_id="p", document_id="d").job_id.startswith("job_")


def test_defaults():
    assert Project(name="a").baseline == Baseline.GENERIC_800_53
    assert (
        Document(project_id="p", filename="f", s3_key="k").status == DocumentStatus.UPLOAD_PENDING
    )
    assert ParseJob(project_id="p", document_id="d").status == JobStatus.PENDING


def test_project_item_round_trip():
    project = Project(name="Sys", baseline=Baseline.FEDRAMP_MODERATE, created_by="jdoe")
    item = _to_item(project)
    restored = Project(**_strip_keys(item))
    assert restored == project


def test_section_item_round_trip():
    section = Section(
        document_id="doc_1",
        project_id="proj_1",
        order=3,
        level=2,
        heading="AC-2",
        parent_id="sec_root",
        text="body",
        char_length=4,
    )
    restored = Section(**_strip_keys(_to_item(section)))
    assert restored == section


def test_parse_key_prefers_the_converted_docx():
    doc = Document(
        project_id="proj_1",
        filename="policy.doc",
        s3_key="projects/p/documents/d.doc",
        source_format="doc",
        converted_s3_key="projects/p/documents/d.converted.docx",
    )
    assert doc.parse_key() == "projects/p/documents/d.converted.docx"


def test_parse_key_is_the_upload_when_nothing_was_converted():
    doc = Document(
        project_id="proj_1", filename="policy.docx", s3_key="projects/p/documents/d.docx"
    )
    assert doc.converted_s3_key is None
    assert doc.parse_key() == "projects/p/documents/d.docx"

"""Domain models.

These are the persisted shapes for the ingest & parse pipeline (M1). The data
model is deliberately project-scoped from day one: a Project is one system's A&A
package and holds many Documents, each of which parses into many Sections. Later
milestones attach control mappings and decision-log entries to Sections without
reshaping this core.

Pydantic is used for validation and (de)serialization to/from DynamoDB item
dicts. Nothing here stores document *body* text beyond what a Section needs; the
original .docx always lives in S3, addressed by ``s3_key``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(UTC)


class Baseline(enum.StrEnum):
    """Selectable control baseline context; drives mapping/suggestion logic later."""

    DOD_CNSSI_1253 = "dod_cnssi_1253"
    FEDRAMP = "fedramp"
    FIPS_199_LOW = "fips199_low"
    FIPS_199_MODERATE = "fips199_moderate"
    FIPS_199_HIGH = "fips199_high"
    GENERIC_800_53 = "generic_800_53"


class JobStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DocumentStatus(enum.StrEnum):
    UPLOADED = "uploaded"  # object in S3, not yet parsed
    PARSING = "parsing"
    PARSED = "parsed"  # sections extracted; ready for control mapping
    MAPPING = "mapping"  # LLM proposing section -> control mappings
    MAPPED = "mapped"  # proposals ready for human review
    MAPPING_APPROVED = "mapping_approved"  # human confirmed mapping; ready for drafting
    DRAFTING = "drafting"  # LLM drafting Rev 5 text per section
    DRAFTED = "drafted"  # Rev 5 drafts ready for human review/edit
    EXPORTING = "exporting"  # building the Rev 5 .docx
    EXPORTED = "exported"  # Rev 5 .docx available for download
    FAILED = "failed"


class MappingStatus(enum.StrEnum):
    """Per-section control-mapping review state (the human checkpoint)."""

    PROPOSED = "proposed"  # LLM proposal, not yet reviewed
    EDITED = "edited"  # human changed the control set
    APPROVED = "approved"  # human confirmed (LLM proposal or their edit)


class DraftStatus(enum.StrEnum):
    """Per-section Rev 5 draft review state."""

    PROPOSED = "proposed"  # LLM draft, not yet reviewed
    EDITED = "edited"  # human edited the draft text
    APPROVED = "approved"  # human confirmed the draft


class Project(BaseModel):
    """One system's A&A package."""

    project_id: str = Field(default_factory=lambda: _new_id("proj"))
    name: str
    baseline: Baseline = Baseline.GENERIC_800_53
    created_at: datetime = Field(default_factory=_now)
    created_by: str = "anonymous"
    document_count: int = 0


class Section(BaseModel):
    """A parsed unit of a document — a heading and the text beneath it.

    Sections form a tree via ``parent_id`` and ``level`` (heading depth). Control
    mapping (M2) attaches to sections by id.
    """

    section_id: str = Field(default_factory=lambda: _new_id("sec"))
    document_id: str
    project_id: str
    order: int  # 0-based position in document reading order
    level: int  # heading depth; 0 for the synthetic root/preamble
    heading: str = ""  # heading text ("" for preamble)
    parent_id: str | None = None
    text: str = ""  # body text under this heading, above child headings
    char_length: int = 0  # convenience for UI/logging without loading text


class Document(BaseModel):
    """An uploaded source policy document."""

    document_id: str = Field(default_factory=lambda: _new_id("doc"))
    project_id: str
    filename: str
    s3_key: str
    status: DocumentStatus = DocumentStatus.UPLOADED
    uploaded_at: datetime = Field(default_factory=_now)
    uploaded_by: str = "anonymous"
    section_count: int = 0
    parse_error: str | None = None
    export_key: str | None = None  # S3 key of the generated Rev 5 .docx, once exported


class ParseJob(BaseModel):
    """Tracks an async parse worker invocation for one document."""

    job_id: str = Field(default_factory=lambda: _new_id("job"))
    project_id: str
    document_id: str
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    error_type: str | None = None


class MappingJob(BaseModel):
    """Tracks an async control-mapping worker invocation for one document."""

    job_id: str = Field(default_factory=lambda: _new_id("mjob"))
    project_id: str
    document_id: str
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    section_count: int = 0
    error_type: str | None = None


class ControlMapping(BaseModel):
    """One section's mapping to Rev 4 control(s).

    The LLM proposes ``proposed_control_ids`` with a ``confidence``; a human then
    reviews and, on approval, ``final_control_ids`` holds the authoritative set.
    Downstream Rev 5 drafting (M3) reads only APPROVED mappings.
    """

    mapping_id: str = Field(default_factory=lambda: _new_id("map"))
    project_id: str
    document_id: str
    section_id: str
    order: int  # mirrors the section's document-order for stable listing

    proposed_control_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    rationale: str = ""  # brief LLM justification; stored (encrypted), never logged

    final_control_ids: list[str] | None = None  # set on human edit/approve
    status: MappingStatus = MappingStatus.PROPOSED
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    def effective_control_ids(self) -> list[str]:
        """Human-confirmed set if present, else the LLM proposal."""
        return (
            self.final_control_ids
            if self.final_control_ids is not None
            else self.proposed_control_ids
        )


class DispositionNote(BaseModel):
    """How one Rev 4 control maps forward to Rev 5, carried onto a draft."""

    rev4_id: str
    rev5_ids: list[str] = Field(default_factory=list)
    relationship: str  # same | renamed | withdrawn | new | merged | split | incorporated


class DraftJob(BaseModel):
    """Tracks an async Rev 5 drafting worker invocation for one document."""

    job_id: str = Field(default_factory=lambda: _new_id("djob"))
    project_id: str
    document_id: str
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    section_count: int = 0
    error_type: str | None = None


class ExportJob(BaseModel):
    """Tracks an async Rev 5 DOCX export for one document."""

    job_id: str = Field(default_factory=lambda: _new_id("xjob"))
    project_id: str
    document_id: str
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    error_type: str | None = None


class Draft(BaseModel):
    """One section's proposed Rev 5 policy text.

    Built from the section's APPROVED control mapping: the Rev 4 controls are
    carried forward to their Rev 5 equivalents via the crosswalk, and the LLM
    drafts updated policy language plus improvement suggestions. A human edits
    and approves; ``edited_text`` (when set) is authoritative for M4 export.
    """

    draft_id: str = Field(default_factory=lambda: _new_id("draft"))
    project_id: str
    document_id: str
    section_id: str
    order: int  # mirrors the section's document-order

    rev4_control_ids: list[str] = Field(default_factory=list)
    rev5_control_ids: list[str] = Field(default_factory=list)
    dispositions: list[DispositionNote] = Field(default_factory=list)

    draft_text: str = ""  # LLM proposal (encrypted at rest; never logged)
    suggestions: list[str] = Field(default_factory=list)

    edited_text: str | None = None  # human edit
    status: DraftStatus = DraftStatus.PROPOSED
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    def effective_text(self) -> str:
        """Human-edited text if present, else the LLM draft."""
        return self.edited_text if self.edited_text is not None else self.draft_text

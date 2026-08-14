"""eMASS control-implementation export.

eMASS (the DoD RMF system of record) tracks a system's NIST 800-53 Rev 5
implementation per control: a narrative, plus artifacts (the policy documents)
associated to each control. This export emits exactly that shape — one row per
Rev 5 control the package implements, the approved narrative text, and the
exported Rev 5 .docx filename to attach as the artifact — so populating an
eMASS record is transcription, not re-derivation.

Only APPROVED drafts contribute: eMASS entries assert what the organization
does, so unreviewed LLM text must never leak into them.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from rmf_migrator.common.catalog import rev5_catalog
from rmf_migrator.common.csv_safe import csv_safe_row
from rmf_migrator.common.models import Draft, DraftStatus

COLUMNS = [
    "control_id",
    "control_title",
    "implementation_narrative",
    "artifact",
    "source_sections",
    "approved_by",
    "approved_at",
]

_CONTROL_ID_RE = re.compile(r"^([A-Z]{2,3})-(\d+)(?:\((\d+)\))?$")


@dataclass(frozen=True)
class DraftSource:
    """An approved draft plus the document context eMASS rows need."""

    draft: Draft
    filename: str  # source document filename (pre-export)
    heading: str  # section heading the narrative came from


def _artifact_name(filename: str) -> str:
    """Name of the exported Rev 5 .docx — the artifact attached in eMASS.

    Mirrors the export download convention (handlers/export.py): the source
    document's stem plus ``-rev5.docx``.
    """
    stem = filename[:-5] if filename.lower().endswith(".docx") else filename
    return f"{stem}-rev5.docx"


def _control_sort_key(control_id: str) -> tuple:
    """Family, base number, enhancement number — AC-2 before AC-2(1) before AC-10."""
    match = _CONTROL_ID_RE.match(control_id)
    if not match:
        return (control_id, 0, 0)
    family, base, enhancement = match.groups()
    return (family, int(base), int(enhancement) if enhancement else 0)


def build_rows(sources: list[DraftSource]) -> list[dict[str, str]]:
    catalog = rev5_catalog()

    by_control: dict[str, list[DraftSource]] = {}
    for source in sources:
        if source.draft.status != DraftStatus.APPROVED:
            continue
        for control_id in source.draft.rev5_control_ids:
            by_control.setdefault(control_id, []).append(source)

    rows: list[dict[str, str]] = []
    for control_id in sorted(by_control, key=_control_sort_key):
        contributing = by_control[control_id]
        narrative_parts = []
        section_refs = []
        artifacts = []
        reviewed = [
            (s.draft.reviewed_at, s.draft.reviewed_by)
            for s in contributing
            if s.draft.reviewed_at is not None
        ]
        for source in contributing:
            where = f"{source.filename} § {source.heading}" if source.heading else source.filename
            section_refs.append(where)
            narrative_parts.append(f"[{where}] {source.draft.effective_text()}")
            artifact = _artifact_name(source.filename)
            if artifact not in artifacts:
                artifacts.append(artifact)

        latest_at, latest_by = max(reviewed, default=(None, None))
        control = catalog.get(control_id)
        rows.append(
            {
                "control_id": control_id,
                "control_title": control.title if control else "",
                "implementation_narrative": "\n\n".join(narrative_parts),
                "artifact": "; ".join(artifacts),
                "source_sections": "; ".join(section_refs),
                "approved_by": latest_by or "",
                "approved_at": latest_at.isoformat() if latest_at else "",
            }
        )
    return rows


def to_csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(csv_safe_row(row))
    return buffer.getvalue()

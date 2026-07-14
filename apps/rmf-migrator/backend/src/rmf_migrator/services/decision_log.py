"""Per-control decision log — the audit trail an assessor expects.

For each section it records: the source Rev 4 controls, the Rev 4 -> Rev 5
disposition, the Rev 5 target controls, and the human review trail for both the
mapping and the draft (who / when / status). Exported as CSV.

The log intentionally records *identifiers and review metadata*, not the policy
prose itself, so it can travel more freely than the documents.
"""

from __future__ import annotations

import csv
import io

from rmf_migrator.common.csv_safe import csv_safe_row
from rmf_migrator.common.models import ControlMapping, Draft, Section

COLUMNS = [
    "order",
    "heading",
    "rev4_controls",
    "disposition",
    "rev5_controls",
    "mapping_status",
    "mapping_reviewed_by",
    "mapping_reviewed_at",
    "draft_status",
    "draft_reviewed_by",
    "draft_reviewed_at",
    "draft_finalized",
]


def _disposition_str(draft: Draft | None) -> str:
    if draft is None:
        return ""
    parts = []
    for note in draft.dispositions:
        forward = "/".join(note.rev5_ids) if note.rev5_ids else "—"
        parts.append(f"{note.rev4_id}->{forward} ({note.relationship})")
    return "; ".join(parts)


def build_rows(
    sections: list[Section],
    mappings: list[ControlMapping],
    drafts: list[Draft],
) -> list[dict[str, str]]:
    mapping_by_section = {m.section_id: m for m in mappings}
    draft_by_section = {d.section_id: d for d in drafts}

    rows: list[dict[str, str]] = []
    for section in sorted(sections, key=lambda s: s.order):
        mapping = mapping_by_section.get(section.section_id)
        draft = draft_by_section.get(section.section_id)
        rows.append(
            {
                "order": str(section.order),
                "heading": section.heading,
                "rev4_controls": ", ".join(mapping.effective_control_ids()) if mapping else "",
                "disposition": _disposition_str(draft),
                "rev5_controls": ", ".join(draft.rev5_control_ids) if draft else "",
                "mapping_status": mapping.status.value if mapping else "",
                "mapping_reviewed_by": (mapping.reviewed_by or "") if mapping else "",
                "mapping_reviewed_at": (
                    mapping.reviewed_at.isoformat() if mapping and mapping.reviewed_at else ""
                ),
                "draft_status": draft.status.value if draft else "",
                "draft_reviewed_by": (draft.reviewed_by or "") if draft else "",
                "draft_reviewed_at": (
                    draft.reviewed_at.isoformat() if draft and draft.reviewed_at else ""
                ),
                "draft_finalized": ("yes" if draft and draft.edited_text is not None else "no"),
            }
        )
    return rows


def to_csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(csv_safe_row(row) for row in rows)
    return buffer.getvalue()

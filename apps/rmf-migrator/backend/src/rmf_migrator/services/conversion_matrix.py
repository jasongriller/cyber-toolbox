"""Conversion summary matrix — the hand-to-the-assessor artifact.

One row per Rev 4 control the package addressed: its Rev 4 -> Rev 5 disposition,
the Rev 5 target(s), and where in the package it is now covered (document +
section). Built by inverting each section's control mapping across all of a
project's documents.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from rmf_migrator.common.catalog import baseline_controls, crosswalk, rev4_catalog
from rmf_migrator.common.csv_safe import csv_safe_row

COLUMNS = [
    "rev4_control",
    "rev4_title",
    "disposition",
    "rev5_controls",
    "targets_outside_baseline",
    "source",
    "covered_in",
]


@dataclass(frozen=True)
class Contribution:
    """A single (Rev 4 control -> location) fact from the package."""

    rev4_id: str
    filename: str
    heading: str


def build_rows(
    contributions: list[Contribution], baseline: str | None = None
) -> list[dict[str, str]]:
    cat4 = rev4_catalog()
    cross = crosswalk()
    baseline_ids = baseline_controls(baseline) if baseline else None

    locations: dict[str, set[str]] = {}
    for c in contributions:
        where = f"{c.filename}: {c.heading}" if c.heading else c.filename
        locations.setdefault(c.rev4_id, set()).add(where)

    rows: list[dict[str, str]] = []
    for rev4_id in sorted(locations):
        control = cat4.get(rev4_id)
        row = cross.disposition(rev4_id)
        targets = list(row.rev5_ids) if row and row.rev5_ids else []
        relationship = row.relationship if row else "unknown"
        outside = (
            [t for t in targets if t not in baseline_ids] if baseline_ids is not None else []
        )
        rows.append(
            {
                "rev4_control": rev4_id,
                "rev4_title": control.title if control else "",
                "disposition": relationship,
                "rev5_controls": ", ".join(targets),
                "targets_outside_baseline": ", ".join(outside),
                "source": row.source if row else "",
                "covered_in": "; ".join(sorted(locations[rev4_id])),
            }
        )
    return rows


def to_csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(csv_safe_row(row) for row in rows)
    return buffer.getvalue()

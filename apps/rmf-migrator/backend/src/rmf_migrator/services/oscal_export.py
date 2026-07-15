"""OSCAL component-definition export.

Turns a project's APPROVED Rev 5 drafts into a NIST OSCAL component-definition
(model version 1.1.2) that a GRC platform can import and then assemble into a
system security plan. We emit a component-definition rather than an SSP on
purpose: the tool holds Rev 5 control-implementation narratives (the approved
drafts) but not the system-characteristics an SSP requires (authorization
boundary, categorization, information types, users), so a component-definition
is the honest, complete-in-itself artifact — no fabricated system metadata.

The document is fully deterministic: uuids are UUIDv5 derived from stable keys
and ``last-modified`` comes from the latest draft review time, not the wall
clock, so re-exporting an unchanged project yields byte-identical output.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from rmf_migrator.common.models import Draft, DraftStatus, Project

OSCAL_VERSION = "1.1.2"

# Identifier (not fetched at runtime — private deployments have no egress) for
# the Rev 5 catalog these implementations are written against.
REV5_CATALOG_SOURCE = (
    "https://raw.githubusercontent.com/usnistgov/oscal-content/main"
    "/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json"
)

# Fixed namespace so UUIDv5 values are stable across runs and machines.
_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/Redirishman/rmf-rev5-migrator")

# Namespace URI for our custom props (rev4-source, disposition).
_PROP_NS = "https://github.com/Redirishman/rmf-rev5-migrator/ns/oscal"


def _uuid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, ":".join(parts)))


def _oscal_control_id(display_id: str) -> str:
    """Convert a display id ("AC-2(1)") to its OSCAL form ("ac-2.1")."""
    return display_id.lower().replace("(", ".").replace(")", "")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _last_modified(project: Project, approved: list[Draft]) -> str:
    review_times = [d.reviewed_at for d in approved if d.reviewed_at is not None]
    return _iso(max(review_times)) if review_times else _iso(project.created_at)


def _implemented_requirements(project: Project, approved: list[Draft]) -> list[dict]:
    """One implemented-requirement per covered Rev 5 control, in id order.

    A section's draft can target several Rev 5 controls (a split), and several
    sections can address the same control; both are aggregated so each Rev 5
    control appears exactly once with the contributing statements and the Rev 4
    provenance behind it.
    """
    # rev5 control id -> {"texts": [...], "provenance": [(rev4_id, relationship)]}
    by_control: dict[str, dict] = {}
    for draft in approved:
        text = draft.effective_text().strip()
        provenance = [(n.rev4_id, n.relationship) for n in draft.dispositions]
        for rev5_id in draft.rev5_control_ids:
            entry = by_control.setdefault(rev5_id, {"texts": [], "provenance": []})
            if text:
                entry["texts"].append(text)
            for item in provenance:
                if item not in entry["provenance"]:
                    entry["provenance"].append(item)

    requirements: list[dict] = []
    for rev5_id in sorted(by_control):
        entry = by_control[rev5_id]
        rev4_sources = list(dict.fromkeys(rev4_id for rev4_id, _ in entry["provenance"]))
        dispositions = list(dict.fromkeys(rel for _, rel in entry["provenance"] if rel))
        props = [{"name": "rev4-source", "value": r, "ns": _PROP_NS} for r in rev4_sources]
        props += [{"name": "disposition", "value": rel, "ns": _PROP_NS} for rel in dispositions]
        requirement = {
            "uuid": _uuid(project.project_id, "ir", rev5_id),
            "control-id": _oscal_control_id(rev5_id),
            "description": "\n\n".join(entry["texts"]),
        }
        if props:
            requirement["props"] = props
        requirements.append(requirement)
    return requirements


def build_component_definition(project: Project, drafts: list[Draft]) -> dict:
    """Build an OSCAL component-definition document for a project's drafts."""
    approved = [d for d in drafts if d.status == DraftStatus.APPROVED]

    control_implementation = {
        "uuid": _uuid(project.project_id, "control-implementation"),
        "source": REV5_CATALOG_SOURCE,
        "description": (
            "SP 800-53 Rev 5 control implementations converted from the system's "
            "Rev 4 authorization package by rmf-rev5-migrator."
        ),
        "implemented-requirements": _implemented_requirements(project, approved),
    }

    component = {
        "uuid": _uuid(project.project_id, "component"),
        "type": "software",
        "title": project.name,
        "description": (
            f"Documented SP 800-53 Rev 5 control implementations for {project.name}. "
            "Import into a GRC tool and assemble into a system security plan with "
            "the system-characteristics this tool does not capture."
        ),
        "control-implementations": [control_implementation],
    }

    return {
        "component-definition": {
            "uuid": _uuid(project.project_id, "component-definition"),
            "metadata": {
                "title": f"{project.name} — SP 800-53 Rev 5 control implementation",
                "last-modified": _last_modified(project, approved),
                "version": "1.0",
                "oscal-version": OSCAL_VERSION,
            },
            "components": [component],
        }
    }

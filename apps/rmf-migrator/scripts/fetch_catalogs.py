#!/usr/bin/env python3
"""Fetch and normalize the official NIST SP 800-53 control catalogs.

Downloads the authoritative OSCAL catalogs (public domain, U.S. Government work)
for Rev 4 and Rev 5 from the NIST oscal-content repository, and writes slim,
normalized JSON into ``data/catalogs/`` plus a derived Rev 4 -> Rev 5 disposition
map into ``data/mappings/``.

Why bundle instead of fetch at runtime: private/GovCloud deployments have no
internet egress, so the tool ships this data inside the repo/artifact. A
maintainer runs this script (with internet) to (re)generate the committed files.

Usage:
    python scripts/fetch_catalogs.py

The derived mapping captures relationships that are computable from the two
catalogs alone: controls present in both ("same" / "renamed") and added in Rev 5
("new"). For controls dropped in Rev 5 it additionally reads the editorial
"where did it go" judgment that NIST encodes directly on each withdrawn control
in the OSCAL catalog via ``incorporated-into`` and ``moved-to`` links (the same
information published in the "SP 800-53 Rev 5 to Rev 4 comparison workbook"):

  * ``moved``        -- renumbered to a single new Rev 5 id (moved-to link)
  * ``incorporated`` -- folded into one Rev 5 control (single incorporated-into)
  * ``split``        -- folded into several Rev 5 controls (multiple links)
  * ``withdrawn``    -- dropped with no successor control

Each row's ``source`` records whether it came from the plain id diff or from the
catalog's successor links, so the app can show how much confidence to place in it.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

REPO_RAW = "https://raw.githubusercontent.com/usnistgov/oscal-content/main"
REV4_URL = f"{REPO_RAW}/nist.gov/SP800-53/rev4/json/NIST_SP-800-53_rev4_catalog.json"
REV5_URL = f"{REPO_RAW}/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json"

# Rev 5 baseline profiles (LOW / MODERATE / HIGH). Each lists the control ids the
# baseline requires, used for package coverage/gap analysis.
REV5_BASELINE_URLS = {
    "low": f"{REPO_RAW}/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_LOW-baseline_profile.json",
    "moderate": f"{REPO_RAW}/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_MODERATE-baseline_profile.json",
    "high": f"{REPO_RAW}/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_HIGH-baseline_profile.json",
}

ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = ROOT / "data" / "catalogs"
MAPPING_DIR = ROOT / "data" / "mappings"
BASELINE_DIR = ROOT / "data" / "baselines"


def _download_json(url: str) -> dict[str, Any]:
    print(f"downloading {url}", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": "rmf-rev5-migrator"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.load(resp)


def _canonical_id(oscal_id: str) -> str:
    """Canonical display id derived from an OSCAL id.

    The OSCAL id (e.g. "ac-11.1") has an identical format across Rev 4 and Rev 5,
    whereas the human 'label' prop is zero-padded inconsistently between the two
    revisions (Rev 5 emits "AC-11(01)", Rev 4 "AC-11(1)"). Deriving the display id
    from the OSCAL id gives one canonical form ("AC-11(1)") that joins cleanly
    across revisions and against baseline profiles.
    """
    parts = oscal_id.split(".")
    family, _, number = parts[0].partition("-")
    display = f"{family.upper()}-{number}"
    for enhancement in parts[1:]:
        display += f"({enhancement})"
    return display


def _display_id(control: dict[str, Any]) -> str:
    return _canonical_id(control["id"])


def extract_baseline_ids(profile_json: dict[str, Any]) -> list[str]:
    """Pull the canonical control ids a baseline profile requires."""
    ids: list[str] = []
    for imp in profile_json.get("profile", {}).get("imports", []):
        for inc in imp.get("include-controls", []):
            for oscal_id in inc.get("with-ids", []):
                ids.append(_canonical_id(oscal_id))
    # De-duplicate, keep sorted for stable diffs.
    return sorted(set(ids))


def _is_withdrawn(control: dict[str, Any]) -> bool:
    for prop in control.get("props", []):
        if prop.get("name") == "status" and prop.get("value") == "withdrawn":
            return True
    return False


# Successor link relations NIST places on a withdrawn control to say where its
# requirement went in Rev 5.
_SUCCESSOR_RELS = ("incorporated-into", "moved-to")


def _href_to_id(href: str) -> str:
    """Resolve an OSCAL link href to a canonical control display id.

    Hrefs point at a control (``#ac-6``) or a statement within one
    (``#ac-2_smt.k``); both resolve to the containing control ("AC-6" / "AC-2").
    A family-level href (``#sr``) resolves to a non-control ("SR-") and is
    filtered out later by validating targets against the real Rev 5 id set.
    """
    oscal_id = href.lstrip("#").split("_", 1)[0]
    return _canonical_id(oscal_id)


def _successor_links(control: dict[str, Any]) -> list[tuple[str, str]]:
    """(relation, canonical target id) pairs from a control's successor links."""
    out: list[tuple[str, str]] = []
    for link in control.get("links", []):
        rel = link.get("rel")
        href = link.get("href")
        if rel in _SUCCESSOR_RELS and href:
            out.append((rel, _href_to_id(href)))
    return out


def _walk_controls(
    node: dict[str, Any], family: str, out: list[dict[str, Any]]
) -> None:
    for control in node.get("controls", []):
        out.append(
            {
                "id": _display_id(control),
                "oscal_id": control["id"],
                "title": control.get("title", "").strip(),
                "family": family,
                "is_enhancement": "." in control["id"],
                "withdrawn": _is_withdrawn(control),
                "successors": _successor_links(control),
            }
        )
        # Enhancements are nested controls.
        _walk_controls(control, family, out)


def normalize_catalog(catalog_json: dict[str, Any]) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = []
    for group in catalog_json["catalog"].get("groups", []):
        family = group.get("id", "").upper()
        _walk_controls(group, family, controls)
    controls.sort(key=lambda c: c["oscal_id"])
    return controls


def _dropped_disposition(
    successors: list[tuple[str, str]], rev5_ids: set[str]
) -> tuple[str, list[str]]:
    """Classify a Rev 4 control that is not carried forward under its own id.

    Uses the withdrawn control's successor links, keeping only targets that are
    real, non-withdrawn Rev 5 controls (drops family-level and enhancement-only
    pointers). Returns (relationship, ordered unique rev5 target ids).
    """
    seen: set[str] = set()
    moved: list[str] = []
    incorporated: list[str] = []
    for rel, target in successors:
        if target not in rev5_ids or target in seen:
            continue
        seen.add(target)
        (moved if rel == "moved-to" else incorporated).append(target)

    if incorporated:
        # A control folded into >1 target was split; into exactly one, incorporated.
        # Any moved-to targets are unusual alongside incorporated-into but kept.
        targets = list(dict.fromkeys(incorporated + moved))
        return ("split" if len(targets) > 1 else "incorporated"), targets
    if moved:
        return ("moved" if len(moved) == 1 else "split"), moved
    return "withdrawn", []


def derive_mapping(
    rev4: list[dict[str, Any]], rev5: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rev4_ids = {c["id"] for c in rev4 if not c["withdrawn"]}
    rev5_ids = {c["id"] for c in rev5 if not c["withdrawn"]}
    rev5_title = {c["id"]: c["title"] for c in rev5}
    rev4_title = {c["id"]: c["title"] for c in rev4}
    # Successor links live on the withdrawn control in the Rev 5 catalog.
    rev5_successors = {c["id"]: c.get("successors", []) for c in rev5}

    rows: list[dict[str, Any]] = []
    for cid in sorted(rev4_ids):
        if cid in rev5_ids:
            renamed = rev4_title.get(cid) != rev5_title.get(cid)
            rows.append(
                {
                    "rev4_id": cid,
                    "rev5_ids": [cid],
                    "relationship": "renamed" if renamed else "same",
                    "source": "derived:id-diff",
                }
            )
        else:
            relationship, targets = _dropped_disposition(
                rev5_successors.get(cid, []), rev5_ids
            )
            rows.append(
                {
                    "rev4_id": cid,
                    "rev5_ids": targets,
                    "relationship": relationship,
                    "source": "catalog:successor-links"
                    if relationship != "withdrawn"
                    else "derived:id-diff",
                }
            )
    # Controls new in Rev 5.
    for cid in sorted(rev5_ids - rev4_ids):
        rows.append(
            {
                "rev4_id": None,
                "rev5_ids": [cid],
                "relationship": "new",
                "source": "derived:id-diff",
            }
        )
    return rows


def main() -> int:
    CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    MAPPING_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    rev4 = normalize_catalog(_download_json(REV4_URL))
    rev5 = normalize_catalog(_download_json(REV5_URL))

    # Successor links are only needed to derive the mapping below; keep them out
    # of the committed catalog files, whose loader ignores them anyway.
    def _catalog_controls(controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{k: v for k, v in c.items() if k != "successors"} for c in controls]

    (CATALOG_DIR / "rev4_controls.json").write_text(
        json.dumps(
            {"revision": "4", "source": REV4_URL, "controls": _catalog_controls(rev4)},
            indent=2,
        )
        + "\n"
    )
    (CATALOG_DIR / "rev5_controls.json").write_text(
        json.dumps(
            {"revision": "5", "source": REV5_URL, "controls": _catalog_controls(rev5)},
            indent=2,
        )
        + "\n"
    )

    mapping = derive_mapping(rev4, rev5)
    (MAPPING_DIR / "rev4_to_rev5.json").write_text(
        json.dumps(
            {
                "note": (
                    "Relationships 'same'/'renamed'/'new' are derived by comparing "
                    "the two catalogs (source 'derived:id-diff'). Controls dropped in "
                    "Rev 5 are classified 'moved'/'incorporated'/'split' from the "
                    "withdrawn control's OSCAL successor links, or 'withdrawn' if it "
                    "has none (source 'catalog:successor-links')."
                ),
                "mappings": mapping,
            },
            indent=2,
        )
        + "\n"
    )

    baseline_counts = {}
    for name, url in REV5_BASELINE_URLS.items():
        ids = extract_baseline_ids(_download_json(url))
        (BASELINE_DIR / f"rev5_{name}.json").write_text(
            json.dumps({"baseline": name, "source": url, "control_ids": ids}, indent=2)
            + "\n"
        )
        baseline_counts[name] = len(ids)

    print(
        f"wrote {len(rev4)} Rev4 controls, {len(rev5)} Rev5 controls, "
        f"{len(mapping)} mapping rows, baselines {baseline_counts}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

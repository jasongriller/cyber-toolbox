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
catalogs alone: controls present in both ("same"), removed in Rev 5
("withdrawn"), and added in Rev 5 ("new"). Relationships that require NIST's
editorial judgment (merged / split / incorporated-into) come from the official
"SP 800-53 Rev 5 to Rev 4 comparison workbook" and should be layered on top;
this script marks the mapping's ``source`` accordingly so the app can show how
much confidence to place in each row.
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

ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = ROOT / "data" / "catalogs"
MAPPING_DIR = ROOT / "data" / "mappings"


def _download_json(url: str) -> dict[str, Any]:
    print(f"downloading {url}", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": "rmf-rev5-migrator"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.load(resp)


def _display_id(control: dict[str, Any]) -> str:
    """Canonical display id derived from the OSCAL id.

    The OSCAL id (e.g. "ac-11.1") has an identical format across Rev 4 and Rev 5,
    whereas the human 'label' prop is zero-padded inconsistently between the two
    revisions (Rev 5 emits "AC-11(01)", Rev 4 "AC-11(1)"). Deriving the display id
    from the OSCAL id gives one canonical form ("AC-11(1)") that joins cleanly
    across revisions.
    """
    parts = control["id"].split(".")
    family, _, number = parts[0].partition("-")
    display = f"{family.upper()}-{number}"
    for enhancement in parts[1:]:
        display += f"({enhancement})"
    return display


def _is_withdrawn(control: dict[str, Any]) -> bool:
    for prop in control.get("props", []):
        if prop.get("name") == "status" and prop.get("value") == "withdrawn":
            return True
    return False


def _walk_controls(node: dict[str, Any], family: str, out: list[dict[str, Any]]) -> None:
    for control in node.get("controls", []):
        out.append(
            {
                "id": _display_id(control),
                "oscal_id": control["id"],
                "title": control.get("title", "").strip(),
                "family": family,
                "is_enhancement": "." in control["id"],
                "withdrawn": _is_withdrawn(control),
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


def derive_mapping(
    rev4: list[dict[str, Any]], rev5: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rev4_ids = {c["id"] for c in rev4 if not c["withdrawn"]}
    rev5_ids = {c["id"] for c in rev5 if not c["withdrawn"]}
    rev5_title = {c["id"]: c["title"] for c in rev5}
    rev4_title = {c["id"]: c["title"] for c in rev4}

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
            rows.append(
                {
                    "rev4_id": cid,
                    "rev5_ids": [],
                    "relationship": "withdrawn",
                    "source": "derived:id-diff",
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

    rev4 = normalize_catalog(_download_json(REV4_URL))
    rev5 = normalize_catalog(_download_json(REV5_URL))

    (CATALOG_DIR / "rev4_controls.json").write_text(
        json.dumps({"revision": "4", "source": REV4_URL, "controls": rev4}, indent=2) + "\n"
    )
    (CATALOG_DIR / "rev5_controls.json").write_text(
        json.dumps({"revision": "5", "source": REV5_URL, "controls": rev5}, indent=2) + "\n"
    )

    mapping = derive_mapping(rev4, rev5)
    (MAPPING_DIR / "rev4_to_rev5.json").write_text(
        json.dumps(
            {
                "note": (
                    "Relationships 'same'/'renamed'/'withdrawn'/'new' are derived "
                    "automatically by comparing the two catalogs. 'merged'/'split'/"
                    "'incorporated' relationships require NIST's official SP 800-53 "
                    "Rev 5 to Rev 4 comparison workbook and should be layered on top."
                ),
                "mappings": mapping,
            },
            indent=2,
        )
        + "\n"
    )

    print(
        f"wrote {len(rev4)} Rev4 controls, {len(rev5)} Rev5 controls, "
        f"{len(mapping)} mapping rows",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

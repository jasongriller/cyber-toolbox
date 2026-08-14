"""Regression tests pinning USER_MANUAL.md's `failed`-status guidance.

Sections 7 and 11 both instruct a user whose document is stuck at `failed`.
Before commit bbb0abc, both rows told the user to re-trigger the document by
re-uploading it as an undifferentiated remedy -- including for convert-stage
failures, where re-uploading a document already in encrypted storage creates
a second document record and a second retained original: the same duplicate-
CUI-copy outcome the .doc-specific rows were fixed to avoid (commit 80f0395).
Both rows now lead with **Retry** and carry the same duplicate-copy caveat.

Nothing else in the test suite reads USER_MANUAL.md, so nothing else would
catch a regression back to the unconditional re-upload wording. This mirrors
the mutation-test discipline that found the original defect: reverting these
two lines to their pre-fix wording must fail this test.
"""

from __future__ import annotations

import re
from pathlib import Path

MANUAL = (Path(__file__).parent.parent.parent / "docs" / "USER_MANUAL.md").read_text(
    encoding="utf-8"
)

# Section 7: the `failed` row of the document-status-reference table.
SECTION_7_ROW = re.search(r"^\| `failed` \| (?P<body>.+) \|$", MANUAL, re.MULTILINE)

# Section 11: the "Document stuck at `failed`" row of the troubleshooting table.
SECTION_11_ROW = re.search(
    r"^\| Document stuck at `failed` \| (?P<body>.+) \|$", MANUAL, re.MULTILINE
)


def test_manual_has_both_failed_status_rows() -> None:
    """Guard the regexes above against the rows moving or being reworded
    out from under this test -- a None match would make every assertion
    below vacuously pass."""
    assert SECTION_7_ROW, "§7 `failed` status row not found in USER_MANUAL.md"
    assert SECTION_11_ROW, "§11 'Document stuck at `failed`' row not found"


def test_section_7_failed_row_points_to_retry_not_unconditional_reupload() -> None:
    body = SECTION_7_ROW.group("body")
    assert "Select **Retry**" in body
    assert "without re-uploading" in body
    # The duplicate-CUI-copy caveat that justifies leading with Retry.
    assert "second document record" in body
    # Pre-fix wording named re-upload as an undifferentiated first resort.
    assert "Re-trigger it (re-upload" not in body


def test_section_11_failed_row_points_to_retry_not_unconditional_reupload() -> None:
    body = SECTION_11_ROW.group("body")
    assert "Select **Retry**" in body
    assert "without re-uploading" in body
    assert "second document record" in body
    # Pre-fix wording offered re-open/re-upload as interchangeable options.
    assert "Re-open it, or re-upload." not in body

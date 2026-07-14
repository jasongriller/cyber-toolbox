"""CSV cell sanitizing for the assessor-facing exports.

Both the decision log and the conversion matrix carry text lifted straight out
of an uploaded document (headings, filenames), and both are meant to be opened
in Excel. A cell beginning with =, +, -, @ (or a leading tab/CR, which Excel
strips before re-testing the first character) is evaluated as a formula, so an
uploaded document whose heading reads `=HYPERLINK("http://x","click")` becomes
a live formula in the assessor's spreadsheet. Prefixing a single quote makes
the cell literal text.
"""

from __future__ import annotations

_FORMULA_PREFIXES = ("=", "+", "-", "@")
_STRIPPED_LEADERS = ("\t", "\r", "\n")


def csv_safe(value: str) -> str:
    """Neutralize a value that a spreadsheet would otherwise read as a formula."""
    if not value:
        return value
    probe = value.lstrip("".join(_STRIPPED_LEADERS))
    if probe.startswith(_FORMULA_PREFIXES) or value[0] in _STRIPPED_LEADERS:
        return "'" + value
    return value


def csv_safe_row(row: dict[str, str]) -> dict[str, str]:
    """Apply csv_safe to every value in a row."""
    return {key: csv_safe(val) for key, val in row.items()}

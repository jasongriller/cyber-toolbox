"""Package-level Rev 5 control coverage and gap analysis.

Aggregates across all of a project's documents: which Rev 5 controls the drafted
package addresses, and — measured against the project's baseline — which required
controls are still gaps. Separately flags Rev 5-*new* controls (e.g. the SR
supply-chain family) that no Rev 4 document carried forward, which is exactly the
blind spot a Rev 4 -> Rev 5 migration creates.

"Covered" means at least one section's draft maps to that Rev 5 control id. This
is coverage of intent, not an assessment of adequacy — a human still judges
whether the language satisfies the control.
"""

from __future__ import annotations

from rmf_migrator.common.catalog import BASELINE_NAMES, baseline_controls, crosswalk
from rmf_migrator.common.models import Baseline, Draft

# Map a project's declared baseline to a Rev 5 baseline control set. FedRAMP and
# CNSSI 1253 don't correspond one-to-one to a NIST baseline (FedRAMP adds
# controls; CNSSI 1253 uses per-C/I/A categorization), so those are documented
# approximations the user can override.
_BASELINE_FOR_PROJECT: dict[Baseline, str | None] = {
    Baseline.FIPS_199_LOW: "low",
    Baseline.FIPS_199_MODERATE: "moderate",
    Baseline.FIPS_199_HIGH: "high",
    Baseline.FEDRAMP: "moderate",
    Baseline.DOD_CNSSI_1253: "high",
    Baseline.GENERIC_800_53: None,
}


def resolve_baseline(project_baseline: Baseline, override: str | None = None) -> str | None:
    """Pick the baseline set to measure against, honoring an explicit override."""
    if override:
        if override not in BASELINE_NAMES:
            raise ValueError(f"unknown baseline {override!r}; expected one of {BASELINE_NAMES}")
        return override
    return _BASELINE_FOR_PROJECT.get(project_baseline)


def covered_controls(drafts: list[Draft]) -> set[str]:
    covered: set[str] = set()
    for draft in drafts:
        covered.update(draft.rev5_control_ids)
    return covered


def build_coverage(drafts: list[Draft], *, baseline_name: str | None) -> dict:
    """Compute coverage + gaps for a project's drafts."""
    covered = covered_controls(drafts)
    new_in_rev5 = set(crosswalk().new_in_rev5())

    result: dict = {
        "baseline": baseline_name,
        "covered_count": len(covered),
        "covered_controls": sorted(covered),
    }

    if baseline_name:
        required = baseline_controls(baseline_name)
        covered_in_baseline = required & covered
        gaps = sorted(required - covered)
        result.update(
            {
                "baseline_total": len(required),
                "baseline_covered": len(covered_in_baseline),
                "coverage_pct": (
                    round(100.0 * len(covered_in_baseline) / len(required), 1) if required else 0.0
                ),
                "baseline_gaps": gaps,
                # New-in-Rev5 controls the baseline requires but nothing covers.
                "new_in_rev5_gaps": sorted((new_in_rev5 & required) - covered),
            }
        )
    else:
        result.update(
            {
                "baseline_total": None,
                "baseline_covered": None,
                "coverage_pct": None,
                "baseline_gaps": [],
                "new_in_rev5_gaps": sorted(new_in_rev5 - covered),
            }
        )
    return result

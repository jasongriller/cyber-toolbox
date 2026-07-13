"""Coverage + gap analysis tests against the real bundled baselines."""

from __future__ import annotations

import pytest

from rmf_migrator.common.catalog import baseline_controls
from rmf_migrator.common.models import Baseline, Draft
from rmf_migrator.services.coverage import (
    build_coverage,
    covered_controls,
    resolve_baseline,
)


def _draft(section_id, rev5_ids):
    return Draft(
        project_id="p",
        document_id="d",
        section_id=section_id,
        order=0,
        rev5_control_ids=list(rev5_ids),
    )


def test_baseline_low_loads_and_has_ac1():
    low = baseline_controls("low")
    assert "AC-1" in low
    assert len(low) > 100


def test_baseline_unknown_raises():
    with pytest.raises(Exception):  # noqa: B017
        baseline_controls("ultra")


def test_resolve_baseline_from_project():
    assert resolve_baseline(Baseline.FIPS_199_LOW) == "low"
    assert resolve_baseline(Baseline.FIPS_199_HIGH) == "high"
    assert resolve_baseline(Baseline.GENERIC_800_53) is None
    # Override wins.
    assert resolve_baseline(Baseline.GENERIC_800_53, "moderate") == "moderate"


def test_resolve_baseline_rejects_bad_override():
    with pytest.raises(ValueError):
        resolve_baseline(Baseline.FIPS_199_LOW, "nonsense")


def test_covered_controls_unions_drafts():
    drafts = [_draft("s1", ["AC-1", "AC-2"]), _draft("s2", ["AC-2", "AU-2"])]
    assert covered_controls(drafts) == {"AC-1", "AC-2", "AU-2"}


def test_build_coverage_reports_baseline_gaps():
    # Cover only AC-1 out of the LOW baseline.
    drafts = [_draft("s1", ["AC-1"])]
    cov = build_coverage(drafts, baseline_name="low")
    low = baseline_controls("low")
    assert cov["baseline"] == "low"
    assert cov["baseline_total"] == len(low)
    assert cov["baseline_covered"] == 1
    assert "AC-1" not in cov["baseline_gaps"]  # it's covered
    assert "AC-14" in cov["baseline_gaps"]  # required by LOW, not covered
    assert 0.0 < cov["coverage_pct"] < 5.0


def test_build_coverage_flags_new_sr_family_gap():
    # Nothing covers the SR family -> it shows in new-in-rev5 gaps.
    cov = build_coverage([_draft("s1", ["AC-1"])], baseline_name="low")
    assert any(cid.startswith("SR-") for cid in cov["new_in_rev5_gaps"])


def test_build_coverage_generic_has_no_baseline_gaps():
    cov = build_coverage([_draft("s1", ["AC-1"])], baseline_name=None)
    assert cov["baseline"] is None
    assert cov["coverage_pct"] is None
    assert cov["baseline_gaps"] == []
    # Still reports new-in-rev5 gaps across the whole Rev 5 delta.
    assert any(cid.startswith("SR-") for cid in cov["new_in_rev5_gaps"])


def test_build_coverage_full_low_coverage_is_100pct():
    drafts = [_draft("s1", list(baseline_controls("low")))]
    cov = build_coverage(drafts, baseline_name="low")
    assert cov["coverage_pct"] == 100.0
    assert cov["baseline_gaps"] == []

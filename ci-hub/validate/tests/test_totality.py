#!/usr/bin/env python3
"""Both-direction tests for the TOTAL / INCREMENTAL / UNKNOWN predicate.

The bar is symmetric on purpose. A predicate that only ever refuses would pass a
one-sided test suite while making every green unlandable; one that only ever
accepts is the defect being fixed. So every rule below is exercised in both
directions: the qualifying case must FIRE, and the violating case must be
REFUSED.

The anchor case is real, not invented: ledger row
``ee3038998fda5250904cb21a7f66a1ce245af87e`` satisfies all five predicates of
``validate_status.rs::is_clean_full_coverage`` with ``result=pass`` while its own
coverage block records 4 of 19 test nodes executed.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from totality import (  # noqa: E402
    INCREMENTAL,
    TOTAL,
    UNKNOWN,
    classify,
    coverage_fraction,
    incremental_chain_depth,
    is_total,
)


def row(**kw):
    """A row that would satisfy is_clean_full_coverage unless overridden."""
    base = {
        "commit": "a" * 40,
        "result": "pass",
        "profile": "full",
        "selection_mode": "full",
        "commit_anchored": True,
        "tree_dirty": False,
    }
    base.update(kw)
    return base


def cov(executed, planned, absent=(), zero=()):
    return {
        "executed_test_nodes": executed,
        "planned_test_nodes": planned,
        "absent_nodes": list(absent),
        "zero_executed_nodes": list(zero),
    }


# --- THE REGRESSION: the real row that is certifiable today -------------------

def test_real_partial_row_is_not_total():
    """ee3038998f: passes every certifier predicate, ran 4 of 19 nodes."""
    r = row(coverage=cov(4, 19, absent=[f"test.n{i}" for i in range(15)]))
    v = classify(r)
    assert v["scope"] == INCREMENTAL, v
    assert is_total(r) is False
    # The denominator must travel with the verdict.
    assert v["executed_test_nodes"] == 4
    assert v["planned_test_nodes"] == 19
    assert "4/19" in v["reason"]


def test_declared_full_cannot_upgrade_observed_partial():
    """profile=full and selection_mode=full must NOT beat observed coverage.
    This is the precedence rule; inverting it re-opens the hole."""
    assert classify(row(profile="full", selection_mode="full",
                        coverage=cov(1, 19)))["scope"] == INCREMENTAL


# --- POSITIVE CONTROL: a genuine total run must be accepted -------------------

def test_genuine_total_run_is_total():
    r = row(coverage=cov(19, 19))
    v = classify(r)
    assert v["scope"] == TOTAL, v
    assert is_total(r) is True
    assert "19/19" in v["reason"]


def test_total_requires_no_absent_and_no_zero_executed_nodes():
    """Counts agreeing is not sufficient: a named node can be absent or inert
    while the totals happen to match."""
    assert classify(row(coverage=cov(19, 19, absent=["test.cli"])))["scope"] == INCREMENTAL
    assert classify(row(coverage=cov(19, 19, zero=["test.cli"])))["scope"] == INCREMENTAL


# --- ABSENCE OF EVIDENCE IS UNKNOWN, NEVER TOTAL ------------------------------

def test_no_coverage_with_full_profile_is_unknown_not_total():
    """86.6% of recorded passes look exactly like this. Reading them as TOTAL is
    the single highest-volume way to get this wrong."""
    r = row()  # profile=full, selection_mode=full, no coverage block
    v = classify(r)
    assert v["scope"] == UNKNOWN, v
    assert is_total(r) is False
    assert v["coverage_evidence"] is False


def test_is_total_is_strict_not_merely_not_incremental():
    """Guards the likely misimplementation `scope != INCREMENTAL`, which would
    promote every UNKNOWN row."""
    assert is_total(row()) is False
    assert classify(row())["scope"] != INCREMENTAL


def test_zero_denominator_is_unknown_not_total():
    assert classify(row(coverage=cov(0, 0)))["scope"] == UNKNOWN


def test_malformed_coverage_is_unknown():
    for bad in (None, [], "full", {"planned_test_nodes": "19"}, {"executed_test_nodes": 4}):
        assert classify(row(coverage=bad))["scope"] in (UNKNOWN, INCREMENTAL)
        assert is_total(row(coverage=bad)) is False
    # booleans must not be read as ints
    assert coverage_fraction(row(coverage=cov(True, 19))) == (None, 19)


# --- A NARROWED DECLARATION DOWNGRADES (safe direction) -----------------------

@pytest.mark.parametrize("profile", [
    "portable-strict-compat-only", "portable-only", "only-portable",
    "quick", "selective", "shallow",
])
def test_narrowed_profile_without_coverage_is_incremental(profile):
    assert classify(row(profile=profile))["scope"] == INCREMENTAL


def test_narrowed_selection_without_coverage_is_incremental():
    assert classify(row(selection_mode="only"))["scope"] == INCREMENTAL


def test_contradiction_between_sources_is_unknown_not_total():
    """Full observed coverage under a narrowed declaration is a disagreement.
    A disagreement must not resolve to the STRONGER claim."""
    v = classify(row(profile="portable-only", coverage=cov(19, 19)))
    assert v["scope"] == UNKNOWN, v
    assert "narrowed" in v["reason"]


# --- CHAIN DEPTH --------------------------------------------------------------

def test_chain_depth_counts_back_to_the_last_total():
    rows = [
        row(commit="c3", coverage=cov(4, 19)),
        row(commit="c2", coverage=cov(2, 19)),
        row(commit="c1", coverage=cov(19, 19)),   # the anchor
        row(commit="c0", coverage=cov(19, 19)),
    ]
    d = incremental_chain_depth(rows)
    assert d["depth"] == 2
    assert d["anchored"] is True
    assert d["anchor_commit"] == "c1"


def test_chain_with_no_total_is_a_lower_bound_not_a_measurement():
    """Reporting depth=N for an unanchored chain would understate drift exactly
    when drift is worst, so `anchored` must say so."""
    rows = [row(commit=f"c{i}", coverage=cov(1, 19)) for i in range(5)]
    d = incremental_chain_depth(rows)
    assert d["depth"] == 5
    assert d["anchored"] is False
    assert "LOWER BOUND" in d["anchor_reason"]


def test_unknown_rows_extend_the_chain_rather_than_anchoring_it():
    """An UNKNOWN row must not silently terminate the chain as if it were a
    verified total run."""
    rows = [row(commit="c2"), row(commit="c1"), row(commit="c0", coverage=cov(19, 19))]
    d = incremental_chain_depth(rows)
    assert d["depth"] == 2 and d["anchor_commit"] == "c0"


def test_immediate_total_is_depth_zero():
    d = incremental_chain_depth([row(coverage=cov(19, 19))])
    assert d["depth"] == 0 and d["anchored"] is True


def test_chain_depth_counts_only_passing_runs_by_default():
    """Regression for a number I reported wrong. Counting every row gave
    depth=69 whose composition was 60 fail / 9 pass / 1 no_result. A failed run
    is not a verification anyone relied on, so it must not inflate the drift
    figure."""
    rows = [
        row(commit="f2", result="fail", coverage=cov(2, 19)),
        row(commit="f1", result="fail", coverage=cov(2, 19)),
        row(commit="p1", result="pass", coverage=cov(4, 19)),
        row(commit="t0", result="pass", coverage=cov(19, 19)),
    ]
    d = incremental_chain_depth(rows)
    assert d["depth"] == 1, d                 # only p1 counts
    assert d["skipped_non_pass"] == 2
    assert d["composition"] == {"fail": 2, "pass": 1}
    assert d["anchor_commit"] == "t0"


def test_chain_depth_can_count_every_run_when_asked():
    rows = [
        row(commit="f1", result="fail", coverage=cov(2, 19)),
        row(commit="p1", result="pass", coverage=cov(4, 19)),
        row(commit="t0", result="pass", coverage=cov(19, 19)),
    ]
    d = incremental_chain_depth(rows, passes_only=False)
    assert d["depth"] == 2 and d["skipped_non_pass"] == 0


def test_chain_depth_always_reports_its_composition():
    """The number must never again be readable without what it is made of."""
    d = incremental_chain_depth([row(commit="x", result="fail", coverage=cov(1, 19))])
    assert "composition" in d and d["composition"] == {"fail": 1}
    assert d["anchored"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

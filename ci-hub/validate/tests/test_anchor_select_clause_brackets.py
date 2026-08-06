#!/usr/bin/env python3
"""Brackets for the `anchor_select` clauses found LIVE BUT UNTESTED.

The adversarial review (ai_docs/phase2-tightening-guards-adversarial-review-20260805.md)
mutation-tested `row_qualifies` and `_coverage_satisfied`: killing any of the
clauses below left the existing suite green, so nothing held them in place. Each
was then shown to be LIVE -- it rejects an input that no other clause rejects --
so they are missing tests, not dead code.

Every case here is built from a row that QUALIFIES at baseline and perturbs
exactly one field, so the refusal is attributable to the named clause and the
bracket cannot pass vacuously. The `test_baseline_row_qualifies` positive control
is what makes that attribution honest: if the baseline ever stops qualifying,
every negative below would pass for the wrong reason.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anchor_select as A  # noqa: E402

PREDICATE = {
    "require": {
        "profile": "full",
        "selection_mode": "full",
        "result": "pass",
        "failures_max": 0,
        "executed_tests_min": 1,
    },
    "counts_schema": 5,
    "coverage": {"per_node": True},
}


def _row(**over: object) -> dict:
    row: dict[str, object] = {
        "commit": "a" * 40,
        "commit_anchored": True,
        "tree_dirty": False,
        "profile": "full",
        "selection_mode": "full",
        "result": "pass",
        "failures": 0,
        "executed_tests": 100,
        "schema_version": 5,
        "coverage": {
            "planned_test_nodes": 10,
            "zero_executed_nodes": [],
            "absent_nodes": [],
        },
    }
    row.update(over)
    return row


def test_baseline_row_qualifies() -> None:
    """POSITIVE CONTROL -- without this the negatives below prove nothing."""
    assert A.row_qualifies(_row(), PREDICATE) == (True, "qualifies")


# --- row_qualifies -----------------------------------------------------------


def test_receipt_without_a_usable_commit_is_refused() -> None:
    """The worst of the set: this would let `commit == "unknown"` be selected
    as a green ANCHOR, i.e. inherit a green from a receipt bound to nothing."""
    for missing in ("unknown", "", None):
        ok, reason = A.row_qualifies(_row(commit=missing), PREDICATE)
        assert (ok, reason) == (False, "no-commit"), missing


def test_failures_above_the_cap_are_refused() -> None:
    ok, reason = A.row_qualifies(_row(failures=5), PREDICATE)
    assert ok is False
    assert reason == "failures=5"


def test_count_capable_receipt_without_executed_tests_is_refused() -> None:
    """schema >= counts_schema: the receipt CAN carry counts, so a missing count
    is a defect rather than an old producer."""
    ok, reason = A.row_qualifies(_row(executed_tests=None), PREDICATE)
    assert ok is False
    assert reason == "count-capable receipt missing executed_tests"


def test_pre_count_receipt_without_executed_tests_is_refused() -> None:
    """schema < counts_schema: an old receipt cannot PROVE nonzero execution, so
    it is refused rather than trusted."""
    ok, reason = A.row_qualifies(
        _row(schema_version=1, executed_tests=None), PREDICATE
    )
    assert ok is False
    assert reason == "pre-count receipt cannot prove nonzero execution"


# --- _coverage_satisfied ------------------------------------------------------


def test_coverage_without_planned_test_nodes_is_refused() -> None:
    for cov in (
        {"zero_executed_nodes": [], "absent_nodes": []},          # field absent
        {"planned_test_nodes": 0, "zero_executed_nodes": [], "absent_nodes": []},
        {"planned_test_nodes": "10", "zero_executed_nodes": [], "absent_nodes": []},
    ):
        ok, reason = A.row_qualifies(_row(coverage=cov), PREDICATE)
        assert ok is False, cov
        assert reason == "count-capable receipt coverage unsatisfied", cov


def test_coverage_with_zero_executed_nodes_is_refused() -> None:
    """A node that discovered tests and executed none is a no-result wearing a
    success badge; completeness is COVERAGE, not a total count."""
    cov = {"planned_test_nodes": 10, "zero_executed_nodes": ["node-a"], "absent_nodes": []}
    ok, reason = A.row_qualifies(_row(coverage=cov), PREDICATE)
    assert ok is False
    assert reason == "count-capable receipt coverage unsatisfied"


def test_coverage_clauses_are_independently_reachable() -> None:
    """Guard against one clause masking another: each perturbation alone flips
    the verdict, so none of the brackets above is riding on a shared failure."""
    # NB `_coverage_satisfied` takes the ROW, not the coverage sub-object.
    assert A.row_qualifies(_row(), PREDICATE)[0] is True
    assert A._coverage_satisfied(_row()) is True
    assert A._coverage_satisfied(
        _row(coverage={"planned_test_nodes": 10, "zero_executed_nodes": [],
                       "absent_nodes": ["node-b"]})
    ) is False
    # A non-dict `coverage` (and an absent one) must be refused, not crash.
    assert A._coverage_satisfied(_row(coverage="not-a-dict")) is False
    assert A._coverage_satisfied({}) is False

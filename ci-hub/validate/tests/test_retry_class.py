#!/usr/bin/env python3
"""Brackets for the typed, fail-closed retry/outcome classifier.

Both directions, always. A classifier that only ever refuses is as useless as
one that only ever certifies, so every refusal below is paired with a positive
case proving the same clause can pass.

The two REAL rows are the measured pair from the live 654-row ledger that
motivated the module: both SIGTERM-interrupted with exit_code 130, differing
only in whether a failure had been counted before the signal arrived. Today
`aggregate.py` stores the first as `fail` and the second as `no_result`; the
whole point of this classifier is that neither loses the interruption.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retry_class import Completion, ProductSignal, RetryClass, classify

# --------------------------------------------------------------------------- #
# The two measured ledger rows (trimmed to the classifying fields).            #
# Paths are NOT reproduced: the real rows carry an owner-home `cwd`, which must #
# never enter a tracked file.                                                   #
# --------------------------------------------------------------------------- #

REAL_INTERRUPTED_WITH_RED = {  # stored `fail` today — the conflation
    "interruption_signal": "TERM", "exit_code": 130, "failures": 1,
    "executed_tests": 408, "gates_run": 5, "gates_expected": None,
    "full_coverage": True, "result": "fail", "raw_result": "fail",
    "reclassified_reason": None,
}

REAL_INTERRUPTED_NO_RED = {  # stored `no_result` today — the correct one
    "interruption_signal": "TERM", "exit_code": 130, "failures": 0,
    "executed_tests": 463, "gates_run": 3, "gates_expected": None,
    "full_coverage": True, "result": "no_result", "raw_result": "fail",
    "reclassified_reason": None,
}

CLEAN_GREEN = {
    "interruption_signal": None, "exit_code": 0, "failures": 0,
    "executed_tests": 740, "full_coverage": True, "result": "pass",
}


# ============================== NEGATIVE ================================== #
# Each must REFUSE to certify, and must do so for the stated reason.          #

def test_interrupted_run_that_saw_a_red_keeps_BOTH_facts():
    """The measured conflation. Neither axis may overwrite the other."""
    o = classify(REAL_INTERRUPTED_WITH_RED)
    assert o.completion is Completion.INTERRUPTED, "the interruption must survive"
    assert o.product is ProductSignal.RED, "the observed red must also survive"
    assert o.certifies is False
    assert o.retry is RetryClass.TRANSIENT, (
        "an interrupted run may answer differently on a retry, even having seen "
        "a red — it never finished its gates"
    )


def test_interrupted_run_without_a_red_is_not_a_product_red():
    o = classify(REAL_INTERRUPTED_NO_RED)
    assert o.completion is Completion.INTERRUPTED
    assert o.product is ProductSignal.GREEN  # it did execute 463 tests, 0 failures
    assert o.certifies is False, "interrupted runs never certify, even looking green"
    assert o.retry is RetryClass.TRANSIENT


def test_the_two_real_rows_are_distinguishable():
    """Today both collapse into one scalar; here they must not."""
    a, b = classify(REAL_INTERRUPTED_WITH_RED), classify(REAL_INTERRUPTED_NO_RED)
    assert a.completion == b.completion, "both were interrupted — that is shared"
    assert a.product != b.product, "but what they OBSERVED differs and must show"


def test_killed_by_bound_is_contention_not_product_red():
    o = classify({"killed_by_bound": 1, "failures": 3, "executed_tests": 10,
                  "full_coverage": True, "result": "timeout"})
    assert o.completion is Completion.KILLED_BY_BOUND
    assert o.retry is RetryClass.TRANSIENT
    assert o.certifies is False


def test_killed_by_signal_is_contention_not_product_red():
    o = classify({"killed_by_signal": 1, "failures": 0, "executed_tests": 5,
                  "full_coverage": True, "result": "killed"})
    assert o.completion is Completion.KILLED_BY_SIGNAL
    assert o.retry is RetryClass.TRANSIENT
    assert o.certifies is False


def test_zero_executed_tests_is_no_result_not_green():
    o = classify({"failures": 0, "executed_tests": 0, "full_coverage": True,
                  "result": "pass"})
    assert o.product is ProductSignal.NONE
    assert o.retry is RetryClass.NO_RESULT
    assert o.certifies is False, "a green with zero tests is a no-result"


def test_unknown_executed_count_cannot_certify():
    """`None` is not zero, but it is not evidence either. Fail closed."""
    o = classify({"failures": 0, "executed_tests": None, "full_coverage": True,
                  "result": "pass"})
    assert o.product is ProductSignal.NONE
    assert o.certifies is False


def test_partial_coverage_cannot_certify():
    o = classify(dict(CLEAN_GREEN, full_coverage=False))
    assert o.product is ProductSignal.GREEN
    assert o.certifies is False
    assert "coverage" in o.reason


def test_empty_row_certifies_nothing():
    o = classify({})
    assert o.certifies is False
    assert o.product is ProductSignal.NONE


def test_completed_red_is_permanent_not_transient():
    o = classify({"failures": 2, "executed_tests": 100, "full_coverage": True,
                  "result": "fail"})
    assert o.completion is Completion.COMPLETED
    assert o.retry is RetryClass.PERMANENT, "retrying re-observes the same red"
    assert o.certifies is False


# ============================== POSITIVE ================================== #
# Proof the classifier is not inert: it certifies when it should.             #

def test_clean_full_green_certifies():
    o = classify(CLEAN_GREEN)
    assert o.completion is Completion.COMPLETED
    assert o.product is ProductSignal.GREEN
    assert o.certifies is True, "the classifier must be able to say yes"
    assert o.retry is RetryClass.PERMANENT


def test_certification_needs_every_clause_so_each_one_bites():
    """Flip one clause at a time; each flip alone must destroy the green."""
    assert classify(CLEAN_GREEN).certifies is True
    for field, bad in (("full_coverage", False), ("executed_tests", 0),
                       ("failures", 1), ("interruption_signal", "TERM"),
                       ("killed_by_bound", 1), ("killed_by_signal", 1),
                       ("result", None)):
        assert classify(dict(CLEAN_GREEN, **{field: bad})).certifies is False, (
            f"flipping {field}={bad!r} alone must prevent certification"
        )


def test_all_four_drain_classes_are_mutually_distinguishable():
    """NO-RESULT / CANCELLED / CONTENTION / PRODUCT-RED, pairwise distinct."""
    rows = {
        "no-result":   {"failures": 0, "executed_tests": 0, "full_coverage": True},
        "cancelled":   {"interruption_signal": "TERM", "failures": 0,
                        "executed_tests": 9, "full_coverage": True},
        "contention":  {"killed_by_bound": 1, "failures": 0, "executed_tests": 9,
                        "full_coverage": True},
        "product-red": {"failures": 1, "executed_tests": 9, "full_coverage": True},
    }
    sigs = {k: (classify(v).completion, classify(v).product, classify(v).retry)
            for k, v in rows.items()}
    assert len(set(sigs.values())) == 4, f"classes collapsed: {sigs}"
    assert all(not classify(v).certifies for v in rows.values())


def test_as_row_keeps_each_axis_in_its_own_column():
    row = classify(REAL_INTERRUPTED_WITH_RED).as_row()
    assert row["completion"] == "interrupted"
    assert row["product_signal"] == "red"      # not overwritten by the interruption
    assert row["retry_class"] == "transient"
    assert row["certifies"] is False
    assert row["classification_reason"]


def test_fixtures_carry_no_owner_path():
    needle = "/" + "home" + "/"
    assert needle not in Path(__file__).read_text(), "fixture leaks an owner path"

#!/usr/bin/env python3
"""anchor_select's coverage predicate must BE the canonical one, not resemble it.

`_coverage_satisfied` gates whether a commit may serve as an ANCHOR, i.e. whether
a later incremental run may INHERIT its green. It used to be a private
re-implementation that DISAGREED with `ci-hub/qualifying_receipt.py`, and every
disagreement was in the permissive direction:

    shape                          canonical   anchor_select (old)
    both lists present, empty      True        True    agree
    zero_executed_nodes MISSING    False       True    DIVERGE, fail-OPEN
    absent_nodes MISSING           False       True    DIVERGE, fail-OPEN
    both lists MISSING             False       True    DIVERGE, fail-OPEN
    zero_executed_nodes null       False       True    DIVERGE, fail-OPEN
    a node is absent               False       False   agree

A missing list means the producer did not report it. Unknown is refused; the
canonical `== []` does that and a truthiness test does not. ci-hub/lib/records.rs
documents the same bug being fixed on the Rust side while Python was strict, so
the permissive spelling surviving here was the other half of a split brain.

The SECOND dimension ran the other way and is bracketed too: the canonical
predicate used `cov.get("planned_test_nodes", 0) > 0`, which RAISES TypeError on
a null or string value where anchor_select refused. A gate that crashes on
malformed input is not a gate, so canonical now type-checks first and refuses --
matching Rust, where the field is typed `u64` and serde refuses at parse time.

The parity test is the one that prevents recurrence: it asserts the two agree on
EVERY shape, so reintroducing a local copy that differs anywhere fails here.
"""

from __future__ import annotations

import sys
from pathlib import Path

VALIDATE = Path(__file__).resolve().parents[1]
CI_HUB = VALIDATE.parent
sys.path.insert(0, str(VALIDATE))
sys.path.insert(0, str(CI_HUB))

import anchor_select as A  # noqa: E402
from qualifying_receipt import coverage_satisfied as canonical  # noqa: E402


def satisfied(cov) -> bool:
    return A._coverage_satisfied({"coverage": cov})


# (name, coverage, expected) -- expected is what BOTH must return.
CASES = [
    # positive control: if this ever stops holding, every negative below passes
    # for the wrong reason.
    ("both lists present and empty", {"planned_test_nodes": 3, "zero_executed_nodes": [], "absent_nodes": []}, True),
    # dimension A: an unreported list is unknown, and unknown is refused.
    ("zero_executed_nodes missing", {"planned_test_nodes": 3, "absent_nodes": []}, False),
    ("absent_nodes missing", {"planned_test_nodes": 3, "zero_executed_nodes": []}, False),
    ("both lists missing", {"planned_test_nodes": 3}, False),
    ("zero_executed_nodes null", {"planned_test_nodes": 3, "zero_executed_nodes": None, "absent_nodes": []}, False),
    ("absent_nodes null", {"planned_test_nodes": 3, "zero_executed_nodes": [], "absent_nodes": None}, False),
    # a reported non-empty list is a real refusal, not an unknown one.
    ("an inert node is named", {"planned_test_nodes": 3, "zero_executed_nodes": ["n"], "absent_nodes": []}, False),
    ("an absent node is named", {"planned_test_nodes": 3, "zero_executed_nodes": [], "absent_nodes": ["n"]}, False),
    # dimension B: malformed planned_test_nodes must REFUSE, never raise.
    ("planned_test_nodes null", {"planned_test_nodes": None, "zero_executed_nodes": [], "absent_nodes": []}, False),
    ("planned_test_nodes string", {"planned_test_nodes": "3", "zero_executed_nodes": [], "absent_nodes": []}, False),
    ("planned_test_nodes zero", {"planned_test_nodes": 0, "zero_executed_nodes": [], "absent_nodes": []}, False),
    ("planned_test_nodes negative", {"planned_test_nodes": -1, "zero_executed_nodes": [], "absent_nodes": []}, False),
    ("planned_test_nodes missing", {"zero_executed_nodes": [], "absent_nodes": []}, False),
    ("planned_test_nodes bool True", {"planned_test_nodes": True, "zero_executed_nodes": [], "absent_nodes": []}, False),
    # the container itself
    ("coverage is not a dict", "nope", False),
    ("coverage is None", None, False),
]


def test_positive_control_a_complete_receipt_is_satisfied():
    cov = CASES[0][1]
    assert canonical(cov) is True
    assert satisfied(cov) is True


def test_every_shape_matches_the_canonical_predicate():
    """THE recurrence guard: a local copy that differs anywhere fails here."""
    mismatches = []
    for name, cov, expected in CASES:
        got_canonical = canonical(cov)
        got_anchor = satisfied(cov)
        if got_canonical != got_anchor:
            mismatches.append(f"{name}: canonical={got_canonical} anchor_select={got_anchor}")
    assert not mismatches, "anchor_select diverged from the canonical predicate:\n" + "\n".join(mismatches)


def test_every_shape_has_the_expected_verdict():
    """Agreement alone is not enough -- both could be wrong together."""
    wrong = [
        f"{name}: expected {expected}, got {satisfied(cov)}"
        for name, cov, expected in CASES
        if satisfied(cov) is not expected
    ]
    assert not wrong, "wrong verdict:\n" + "\n".join(wrong)


def test_an_unreported_list_never_satisfies():
    """The fail-OPEN regression, stated directly: these four were the bug."""
    for name, cov, _ in CASES[1:5]:
        assert satisfied(cov) is False, f"{name} was accepted as covered (fail-open)"
        assert canonical(cov) is False, f"{name} accepted by the canonical predicate"


def test_malformed_planned_test_nodes_refuses_rather_than_raising():
    """The crash regression: a gate that raises is not a gate."""
    for name, cov, _ in CASES[8:10]:
        assert canonical(cov) is False, f"canonical did not refuse {name}"
        assert satisfied(cov) is False, f"anchor_select did not refuse {name}"


def test_counts_sum_with_no_case_dropped():
    """Every case is classified; the totals must add up to the case list."""
    agree = sum(1 for _n, c, _e in CASES if canonical(c) == satisfied(c))
    diverge = len(CASES) - agree
    assert agree + diverge == len(CASES)
    assert diverge == 0, f"{diverge} of {len(CASES)} shapes diverge"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

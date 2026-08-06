#!/usr/bin/env python3
"""Bracket for the in-flight status gate in `classify_check`.

The adversarial review (ai_docs/adv-review-process-infra-artifacts-slice5-20260805.md)
mutation-tested `classify_check` and found ONE surviving clause:

    if normalized_status and normalized_status != "completed":
        return CheckOutcome.NO_RESULT

That clause is the only thing stopping a check that has NOT FINISHED from being
read as a finished result. Deleting it flips `in_progress` + a stale `success`
conclusion to PASSED -- a false green -- and no test noticed.

CALIBRATION, recorded so a later reader does not overstate this: on the live
corpus measured at review time (1,572 checks in `ignored/open-prs-rollup.json`)
ZERO checks would have flipped, because every in-flight check there carries an
empty conclusion and already reaches NO_RESULT by fallthrough. This is
defence-in-depth against a shape the GitHub API can emit, not a fix for an
observed incident. It is bracketed because an untested clause is one refactor
away from deletion, not because it is currently firing.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_outcome import CheckOutcome, classify_check  # noqa: E402


def test_in_flight_check_with_a_stale_success_conclusion_is_not_a_pass() -> None:
    """THE case the clause exists for: a re-run in flight can still carry the
    previous attempt's conclusion. Reading it as PASSED is a false green."""
    assert classify_check("in_progress", "success") is CheckOutcome.NO_RESULT


def test_in_flight_check_with_a_stale_failure_conclusion_is_not_a_fail() -> None:
    """Symmetric: it is equally wrong to bank a red that has not re-run."""
    assert classify_check("queued", "failure") is CheckOutcome.NO_RESULT


def test_every_non_completed_status_is_no_result_whatever_the_conclusion() -> None:
    for status in ("queued", "in_progress", "waiting", "pending", "requested"):
        for conclusion in ("success", "failure", "cancelled", "", None):
            assert classify_check(status, conclusion) is CheckOutcome.NO_RESULT, (
                status,
                conclusion,
            )


def test_completed_status_still_resolves_normally() -> None:
    """POSITIVE CONTROL. Without this the gate could be widened to "always
    NO_RESULT" and the negatives above would all still pass."""
    assert classify_check("completed", "success") is CheckOutcome.PASSED
    assert classify_check("completed", "failure") is CheckOutcome.FAILED
    assert classify_check("completed", "cancelled") is CheckOutcome.NO_RESULT


def test_absent_status_falls_through_to_the_conclusion() -> None:
    """An empty status must NOT be treated as in-flight: some rollup shapes omit
    it entirely and carry only a conclusion."""
    assert classify_check("", "success") is CheckOutcome.PASSED
    assert classify_check(None, "failure") is CheckOutcome.FAILED
    assert classify_check("", "") is CheckOutcome.NO_RESULT


def test_self_timeout_still_wins_over_the_status_gate() -> None:
    """Ordering bracket: our own timeout is a FAILED verdict even though the
    check never reached `completed` on GitHub's side."""
    assert classify_check("in_progress", "", self_timeout=True) is CheckOutcome.FAILED

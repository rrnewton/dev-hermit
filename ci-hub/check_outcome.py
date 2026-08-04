#!/usr/bin/env python3
"""Canonical three-state interpretation of CI check status and conclusion.

A missing answer is not a bad answer and is not a passing answer.  Every caller
must preserve that distinction:

* PASSED: a completed check with conclusion ``success``.
* FAILED: a completed check with a genuine failing conclusion.
* NO_RESULT: cancelled, skipped, neutral, stale, pending, absent, or unknown.

Unknown values deliberately fail into NO_RESULT.  That blocks admission while
remaining recoverable by re-dispatch, and prevents a future GitHub conclusion
from silently becoming either green or red.
"""

from __future__ import annotations

import argparse
from enum import Enum
from typing import Sequence


class CheckOutcome(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NO_RESULT = "NO_RESULT"


PASS_CONCLUSIONS = frozenset(("success",))
FAIL_CONCLUSIONS = frozenset(("failure", "timed_out", "error", "startup_failure"))


def classify_check(
    status: object,
    conclusion: object,
    *,
    self_timeout: bool = False,
) -> CheckOutcome:
    """Classify one check without forcing absence into a Boolean result."""
    normalized_status = str(status or "").strip().lower()
    normalized_conclusion = str(conclusion or "").strip().lower()

    if self_timeout:
        return CheckOutcome.FAILED
    if normalized_status and normalized_status != "completed":
        return CheckOutcome.NO_RESULT
    if normalized_conclusion in PASS_CONCLUSIONS:
        return CheckOutcome.PASSED
    if normalized_conclusion in FAIL_CONCLUSIONS:
        return CheckOutcome.FAILED
    return CheckOutcome.NO_RESULT


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", default="")
    parser.add_argument("--conclusion", default="")
    parser.add_argument("--self-timeout", action="store_true")
    args = parser.parse_args(argv)
    print(
        classify_check(
            args.status,
            args.conclusion,
            self_timeout=args.self_timeout,
        ).value
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

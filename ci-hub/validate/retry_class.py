#!/usr/bin/env python3
"""Typed, fail-closed classification of a validate run's outcome.

WHY THIS EXISTS — a measured conflation, not a hypothetical one.

`ci-hub/validate/aggregate.py:340-360` decides a run's verdict with a
most-severe-first chain::

    fail > timeout > incomplete > no_result(zero tests) > pass-partial > pass

`interruption_signal` does not appear in that chain. It is WRITTEN by
`hermit/validate.sh` (:1422, :1525, :1574), asserted by
`hermit/scripts/test_validate_stop_paths.py`, and — measured 2026-08-07 by
grepping the whole parent — **consumed by nothing**. A typed fact is computed,
serialised, and thrown away, which is the same failure mode already recorded on
this task for `StepOutcome`/`step_failure_reason`.

The consequence, counted on the live 654-row ledger: of the 8 runs interrupted
by SIGTERM, **7 are stored `no_result` and 1 is stored `fail`** — an
interruption wearing a product-red badge, with `reclassified_reason` null. The
classifier fails OPEN into product-red, the most damaging direction: a
no-result that reads as a genuine red is what puts an automated revert of a
healthy main one step away.

THE SHAPE OF THE FIX. The offending row is not simply mislabelled — it is a
COMPOUND state. That run really did observe a product failure AND really was
interrupted before finishing its gates. One scalar `result` cannot hold both,
so one fact always dies; today it is the interruption. So the cure is not a
better `if`/`elif` ordering, it is **separate typed axes**:

* :class:`Completion`    — did the run finish under its own power?
* :class:`ProductSignal` — what did it observe about the product?

Those are independent. Every combination is representable, so nothing is
destroyed, and the four classes the drain needs kept apart — NO-RESULT,
CANCELLED, CONTENTION, PRODUCT-RED — are distinguishable by construction rather
than by a precedence accident.

FAIL-CLOSED means one specific thing here: an interrupted run NEVER certifies,
whatever it observed, because it did not finish its gates. It does NOT mean
rewriting its red into a no-result — that would destroy the product observation
in the other direction and could hide a real failure.

This module is a pure function over already-recorded typed fields. It reads no
logs, runs no subprocess, and greps no text — deliberately, since the
toolchain-dependent text-grep classifier is the thing this task exists to stop
relying on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class Completion(Enum):
    """Did the run reach its own end, and if not, why not?"""

    COMPLETED = "completed"
    """Ran to its own conclusion. Only this value can ever certify."""

    INTERRUPTED = "interrupted"
    """A signal stopped it (validate.sh's `interruption_signal`). CANCELLED."""

    KILLED_BY_BOUND = "killed-by-bound"
    """A wall/CPU/memory bound killed it. CONTENTION — a resource story."""

    KILLED_BY_SIGNAL = "killed-by-signal"
    """Killed by a signal recorded as such (`killed_by_signal`). CONTENTION."""


class ProductSignal(Enum):
    """What the run managed to observe about the product itself."""

    RED = "red"
    """At least one genuine product failure was counted."""

    GREEN = "green"
    """Ran a nonzero number of tests, counted zero failures."""

    NONE = "none"
    """Established nothing: no failures counted AND no tests executed."""


class RetryClass(Enum):
    """Whether re-running could plausibly produce a different answer."""

    PERMANENT = "permanent"
    """A completed run saw a real red. Retrying re-observes the same red."""

    TRANSIENT = "transient"
    """Stopped by interruption/bound/contention. A retry may well answer."""

    NO_RESULT = "no-result"
    """Completed but established nothing. Retry is the only way to learn."""


@dataclass(frozen=True)
class Outcome:
    """The typed verdict. Both axes are kept; neither overwrites the other."""

    completion: Completion
    product: ProductSignal
    retry: RetryClass
    certifies: bool
    reason: str

    def as_row(self) -> dict[str, Any]:
        """Flat, ledger-friendly projection. Every axis stays its own column."""
        return {
            "completion": self.completion.value,
            "product_signal": self.product.value,
            "retry_class": self.retry.value,
            "certifies": self.certifies,
            "classification_reason": self.reason,
        }


def _int(row: Mapping[str, Any], key: str) -> int:
    value = row.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def classify(row: Mapping[str, Any]) -> Outcome:
    """Classify one validate-ledger row from its TYPED fields only.

    Never inspects log text. Unknown/absent fields are treated as the least
    favourable interpretation, so a row that omits evidence cannot certify.
    """
    # --- axis 1: completion. Most specific cause first; these are disjoint. ---
    if _int(row, "killed_by_bound"):
        completion = Completion.KILLED_BY_BOUND
    elif _int(row, "killed_by_signal"):
        completion = Completion.KILLED_BY_SIGNAL
    elif row.get("interruption_signal"):
        completion = Completion.INTERRUPTED
    else:
        completion = Completion.COMPLETED

    # --- axis 2: what it saw. Independent of whether it finished. ---
    failures = _int(row, "failures")
    executed = row.get("executed_tests")
    if failures > 0:
        product = ProductSignal.RED
    elif isinstance(executed, int) and executed > 0:
        product = ProductSignal.GREEN
    else:
        # Zero executed tests, or an unknown count. `None` is NOT zero, but it
        # is also not evidence, and this function is fail-closed.
        product = ProductSignal.NONE

    # --- retry class ---
    if completion is not Completion.COMPLETED:
        retry = RetryClass.TRANSIENT
    elif product is ProductSignal.RED:
        retry = RetryClass.PERMANENT
    elif product is ProductSignal.NONE:
        retry = RetryClass.NO_RESULT
    else:
        retry = RetryClass.PERMANENT  # a completed green needs no retry

    # --- certification: FAIL-CLOSED. Every clause must hold. ---
    certifies = (
        completion is Completion.COMPLETED
        and product is ProductSignal.GREEN
        and bool(row.get("full_coverage"))
        and row.get("result") not in (None, "")
    )

    if completion is not Completion.COMPLETED and product is ProductSignal.RED:
        reason = (
            f"{completion.value} AND a product red was observed; the red is "
            "retained but the run cannot certify (it did not finish its gates)"
        )
    elif completion is not Completion.COMPLETED:
        reason = f"{completion.value}; no product verdict was established"
    elif product is ProductSignal.RED:
        reason = "completed run counted a product failure"
    elif product is ProductSignal.NONE:
        reason = "completed but executed no tests; establishes nothing"
    elif not certifies:
        reason = "completed green but coverage is not full; cannot certify"
    else:
        reason = "completed, full coverage, nonzero tests, zero failures"

    return Outcome(completion, product, retry, certifies, reason)

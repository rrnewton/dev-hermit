#!/usr/bin/env python3
"""THE single source for the cpu/wall KILL SIGNATURE.

A run that dies at a budget is not one thing.  It is either a LIVELOCK -- CPU
burned at ~a full core (or more) for the whole budget, a product defect that
retry can NEVER fix -- or CONTENTION -- low CPU against high wall, the step was
waiting, environmental, and a re-dispatch works.  Opposite causes, opposite
correct responses, and only the cpu/wall RATIO at the kill separates them.

WHY THIS FILE EXISTS.  The ratio test was implemented once in
``ci-hub/history/query.py`` (ledger/step_profiles population) while the run-bundle
attributor ``ci-hub/attribution/attribution.py`` had no cpu signal at all.  Two
consumers of one physical fact, one of them blind, and a standing DRIFT RISK if
a second copy of the thresholds ever appeared.  Both now import from HERE.  If
you change a threshold, change it once, in this file.

WHY THE RATIO IS NOT ENOUGH ON ITS OWN -- two gates that MUST come first:

  1. IT MUST BE A KILL.  A ratio of 0.9 on a run that failed fast in 3s says
     nothing about livelock; it just means the work was CPU-bound.  The
     signature is only meaningful when a budget actually fired.

  2. OOM MUST BE EXCLUDED FIRST.  This is not a nicety -- it is the difference
     between a working classifier and a broken one.  In the live local store,
     OOM-killed rows carry ratios up to 127.5 (e.g. test.strict_compat, 64.5s
     wall / 8221s cpu) because they are massively parallel builds that hit a
     memory ceiling.  Applying ``ratio >= LIVELOCK_RATIO`` without excluding OOM
     would label EVERY ONE of them a livelock.  An OOM is a MEMORY kill,
     orthogonal to the spin question, so it gets its own bucket.

The middle band is deliberately UNDECIDED.  Between CONTENTION_RATIO and
LIVELOCK_RATIO the ratio does not carry the answer, and this module says so
(``ambiguous``) instead of forcing a verdict -- the caller must fall through to
a more expensive decisive test (a low-load control re-run).  Reporting the ratio
alongside the verdict lets a reader audit every boundary call.
"""
from __future__ import annotations

from typing import Optional

# --------------------------------------------------------------------------- thresholds

# On a multi-core box a genuinely blocked/contended step spends most of its wall
# WAITING, so cpu/wall stays low; a spinning step pegs ~one core, so cpu ~= wall.
# Measured livelock signature from the local store:
#   test.detcore_misc    wall 600.013s / cpu 607.785s -> ratio 1.013  (wall_timeout)
#   test.liteinst_strict wall 900.013s / cpu 901.205s -> ratio 1.001  (wall_timeout)
# A ratio >> 1 is legitimately parallel CPU-bound work that ALSO cannot be fixed
# by retry, so it lands in the livelock (retry-futile) bucket alongside single-
# core spin -- provided OOM was excluded first (see module docstring).
LIVELOCK_RATIO = 0.8    # cpu/wall >= this: CPU-bound (>=~one core) -> livelock
CONTENTION_RATIO = 0.3  # cpu/wall <  this: wait-bound -> contention/flake

# --------------------------------------------------------------------------- vocabulary

KILL_OOM = "oom"
KILL_CPU_TIMEOUT = "cpu_timeout"
KILL_WALL_TIMEOUT = "wall_timeout"

LIVELOCK = "livelock"
CONTENTION = "contention"
AMBIGUOUS = "ambiguous"
OOM = "oom"
UNKNOWN = "unknown"

ALL_VERDICTS = (LIVELOCK, CONTENTION, AMBIGUOUS, OOM, UNKNOWN)

# Whether a re-run can plausibly succeed.  This is the axis the FALSE-RED
# question actually turns on: a retry-futile kill is a REAL failure and should
# condemn the commit; a retry-valid kill is a FLAKE and must NOT.  ``None`` =
# not established, which is NOT the same as False -- never let an unknown be
# read as a confirmed real failure.
_RETRY_FUTILE = {
    LIVELOCK: True,
    CONTENTION: False,
    OOM: None,        # depends on whether the ceiling or the workload is wrong
    AMBIGUOUS: None,
    UNKNOWN: None,
}


def cpu_wall_ratio(cpu_s: Optional[float], wall_s: Optional[float]) -> Optional[float]:
    """cpu/wall, or None when the denominator is missing or zero.

    Never divide by a missing denominator: a None ratio must stay None so the
    caller reports UNKNOWN rather than silently treating absent data as 0.0.
    """
    if cpu_s is None or wall_s in (None, 0.0):
        return None
    try:
        return float(cpu_s) / float(wall_s)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def classify_kill(kind: Optional[str], ratio: Optional[float]) -> str:
    """Classify a kill from its killer and its cpu/wall ratio.

    ``kind`` is the budget that fired (``oom``/``cpu_timeout``/``wall_timeout``),
    or None when the run was not killed at all -- in which case this returns
    UNKNOWN, because the signature is only defined at a kill (gate 1).
    """
    if kind is None:
        return UNKNOWN
    if kind == KILL_OOM:          # gate 2 -- MUST precede the ratio test
        return OOM
    if ratio is None:
        return UNKNOWN
    if ratio >= LIVELOCK_RATIO:
        return LIVELOCK
    if ratio < CONTENTION_RATIO:
        return CONTENTION
    return AMBIGUOUS


def retry_futile(verdict: str) -> Optional[bool]:
    """True = retry cannot help (REAL red).  False = retry can (FLAKE).
    None = not established."""
    return _RETRY_FUTILE.get(verdict)


def explain(verdict: str, ratio: Optional[float]) -> str:
    """One auditable sentence carrying the number the verdict rests on."""
    r = "n/a" if ratio is None else f"{ratio:.3f}"
    if verdict == LIVELOCK:
        return (f"cpu/wall={r} >= {LIVELOCK_RATIO} at the budget -- ~a full core "
                "burned for the whole window: LIVELOCK (retry-futile, product-side)")
    if verdict == CONTENTION:
        return (f"cpu/wall={r} < {CONTENTION_RATIO} at the budget -- the step was "
                "WAITING, not computing: CONTENTION (retry-valid, environmental)")
    if verdict == OOM:
        return ("memory ceiling hit -- OOM kill, orthogonal to the spin question; "
                "the cpu/wall ratio is NOT meaningful here")
    if verdict == AMBIGUOUS:
        return (f"cpu/wall={r} sits between {CONTENTION_RATIO} and {LIVELOCK_RATIO} "
                "-- the ratio does not decide; run the low-load control")
    return f"no cpu/wall signature available (ratio={r})"

#!/usr/bin/env python3
"""Detlog comparison producer — the first of the three strict components that had none.

WHY THIS EXISTS. The strict standard is stdout + INFO log + stack + heap. A
mutation test over the scorecard's comparator fields found that only stdout has
producer behaviour: detlog, stack hash and heap hash have none at all, so a
mutation could not plant a value in them because there was nothing there. A cell
claiming strict was claiming checks that do not run. This builds the detlog one.

MODELLED ON `capture_parity`, WHICH IS THE SOUND ONE -- its decision core is
`r == t` over two SHA-256 digests, and 96 observed zeros in the shipped
population prove it fires rather than being always-true.

*** BUT NOT MODELLED ON ITS AXIS, AND THAT IS THE LOAD-BEARING DESIGN CHOICE. ***
`capture_parity` compares a ptrace REFERENCE run against a BACKEND run. Porting
that axis to detlog produces a field that is ALWAYS FALSE, which is exactly as
useless as always-true and fails the same both-halves test. The reason is
measured, not theoretical: the backends emit different numbers of detlog records
for the same guest -- ptrace 141, SaBRe 368, LiteInst 1245
(experiments/detlog-parity-three-backends_20260807). There is no common
denominator, so a cross-backend equality can never be true.

The axis with known-good inputs is SELF-DETERMINISM: run1 vs run2 of the SAME
backend. That is what the three PASS baselines above actually measured (0/141,
0/368, 0/1245), and it is the comparison this module performs.

Cross-backend is still offered, but deliberately CANNOT return a parity boolean
-- see `cross_backend_prefix`.
"""

from __future__ import annotations

import hashlib
import re

#: Detlog records are emitted through `tracing::info!("DETLOG {}", ..)`
#: (hermit/detcore/src/detlog.rs:39), so they arrive on stderr at `--log info`
#: behind whatever prefix the tracing formatter is configured with.
MARKER = "DETLOG"

#: Verdicts. NOT_MEASURED is a THIRD state, never a pass and never a failure --
#: the distinction this whole task exists to preserve.
PASS = "pass"
FAIL = "fail"
NOT_MEASURED = "not-measured"

_TRAILING_WS = re.compile(r"[ \t]+$")


def extract_records(text: str) -> list[str]:
    """Detlog records from raw combined output, in order.

    Normalisation is deliberately minimal and is the same envelope the strict
    comparator uses: drop everything before the marker (that prefix is the
    tracing formatter's wall-clock and level, which is real-time and would make
    every comparison fail for reasons unrelated to determinism) and strip
    trailing whitespace. Nothing INSIDE a record is touched -- virtual time,
    counts, syscall values and addresses all remain compared, because stripping
    them is how a comparator quietly becomes weaker than it claims.
    """
    out: list[str] = []
    for line in text.splitlines():
        idx = line.find(MARKER)
        if idx < 0:
            continue
        out.append(_TRAILING_WS.sub("", line[idx:]))
    return out


def digest(records: list[str]) -> str:
    """SHA-256 over the record stream -- the same shape `capture_parity` uses."""
    h = hashlib.sha256()
    for r in records:
        h.update(r.encode("utf-8", "surrogateescape"))
        h.update(b"\n")
    return h.hexdigest()


def common_prefix(a: list[str], b: list[str]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def self_determinism(run1: str, run2: str) -> dict:
    """Compare two runs of the SAME backend on the same guest.

    Returns a record carrying its own denominator, because a bare verdict cannot
    be audited: "pass" over zero records and "pass" over 1245 records are
    different facts and must not print the same.
    """
    a = extract_records(run1)
    b = extract_records(run2)

    # EMPTY IS NOT AGREEMENT. Two empty streams have an identical digest and
    # would sail through an `r == t` check as a pass -- a green produced by
    # measuring nothing, which is the precise failure this task was created
    # from. Refuse it as a third state instead.
    if not a or not b:
        return {
            "verdict": NOT_MEASURED,
            "reason": f"empty detlog stream (run1={len(a)} run2={len(b)} records); "
            "a comparison over zero records is not a pass",
            "denominator_run1": len(a),
            "denominator_run2": len(b),
            "differing": None,
            "common_prefix": 0,
            "digest_run1": None,
            "digest_run2": None,
        }

    d1, d2 = digest(a), digest(b)
    # Positional mismatches PLUS the length delta: a truncated stream that agrees
    # everywhere it overlaps is still a divergence, and counting only positional
    # mismatches would score it 0 and call it clean.
    positional = sum(1 for x, y in zip(a, b) if x != y)
    differing = positional + abs(len(a) - len(b))
    return {
        "verdict": PASS if d1 == d2 else FAIL,
        "reason": "" if d1 == d2 else f"{differing} of {max(len(a), len(b))} records differ",
        "denominator_run1": len(a),
        "denominator_run2": len(b),
        "differing": differing,
        "common_prefix": common_prefix(a, b),
        "digest_run1": d1,
        "digest_run2": d2,
    }


def cross_backend_prefix(a_text: str, b_text: str) -> dict:
    """Cross-backend detlog relationship -- WITHOUT a parity boolean.

    This function refuses to return pass/fail on purpose. The backends emit
    different record counts for the same guest (measured: 141 / 368 / 1245), so
    an equality verdict is false by construction on every cell, and a single
    "parity percentage" has no defined denominator. What IS meaningful is how
    far the two streams agree before they part, reported against BOTH
    denominators so neither can be quoted alone.
    """
    a = extract_records(a_text)
    b = extract_records(b_text)
    pre = common_prefix(a, b)
    return {
        "comparable": False,
        "reason": "detlog record counts are backend-specific; equality is not a "
        "defined comparison across backends",
        "denominator_a": len(a),
        "denominator_b": len(b),
        "common_prefix": pre,
        "prefix_over_a_pct": round(100.0 * pre / len(a), 1) if a else None,
        "prefix_over_b_pct": round(100.0 * pre / len(b), 1) if b else None,
    }


def tier_for(components: dict[str, str]) -> str:
    """Name the components actually compared -- never 'strict' on a subset.

    The strict standard is stdout + INFO log + stack + heap. Until every one has
    a producer, a cell must say which were compared rather than inherit a label
    for checks that did not run. That is what the tier field is for.
    """
    done = sorted(k for k, v in components.items() if v == PASS)
    if not done:
        return ""
    required = {"stdout", "info_log", "stack", "heap"}
    if required.issubset(set(done)):
        return "strict"
    return "partial:" + "+".join(done)

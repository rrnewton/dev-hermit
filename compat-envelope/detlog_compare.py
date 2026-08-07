#!/usr/bin/env python3
"""Detlog verdict — stable entry point for the collector.

THIS FILE IS A CONTRACT SURFACE. hermit-w7's collector loads it BY PATH and calls
`self_determinism` (matrix_score.py:43,54,210), so neither the path nor that
symbol moves.

THE COMPARISON LOGIC LIVES IN `strict_verdict.py`, not here. Under the ratified
split this module renders verdicts and w7 renders collection; keeping two
implementations that both compare detlog is the drift the split exists to
prevent, and it would be silent, since both would go on returning plausible
verdicts while disagreeing. `strict_verdict` also covers stack and heap, so one
comparison rule serves all three components rather than each drifting into its
own weaker version — which is how `bitwise_parity` ended up hardcoded while
`parity` stayed real.

The dict returned below keeps its ORIGINAL key names (`denominator_run1/_run2`,
`digest_run1/_run2`) so the collector's existing wiring and assertions are
unaffected by the move.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import strict_verdict as _sv  # noqa: E402

MARKER = _sv.MARKER
PASS = _sv.PASS
FAIL = _sv.FAIL
NOT_MEASURED = _sv.NOT_MEASURED


def extract_records(text: str) -> list[str]:
    """Detlog records in order, wall-clock prefix normalised away."""
    return _sv.extract(text, "detlog")


def digest(records: list[str]) -> str:
    return _sv.digest(records)


def common_prefix(a: list[str], b: list[str]) -> int:
    return _sv.common_prefix(a, b)


def self_determinism(run1: str, run2: str) -> dict:
    """Detlog self-determinism: run1 vs run2 of the SAME backend.

    Self-determinism, not cross-backend, because the backends emit different
    record counts for the same guest (measured 141 / 368 / 1245) — cross-backend
    equality is false by construction and would fire on every cell.
    """
    v = _sv.detlog_verdict(run1, run2)
    return {
        "verdict": v["verdict"],
        "reason": v["reason"],
        "denominator_run1": v["denominator_a"],
        "denominator_run2": v["denominator_b"],
        "differing": v["differing"],
        "common_prefix": v["common_prefix"],
        "digest_run1": v["digest_a"],
        "digest_run2": v["digest_b"],
    }


def cross_backend_prefix(a_text: str, b_text: str) -> dict:
    """Cross-backend relationship with NO parity boolean. See strict_verdict."""
    return _sv.cross_backend_prefix(a_text, b_text, "detlog")


def tier_for(components: dict) -> str:
    """Never `strict` on a subset; `partial:<components>` until all four exist."""
    return _sv.compose_tier(components)

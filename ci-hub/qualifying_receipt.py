#!/usr/bin/env python3
"""The SINGLE qualifying-receipt predicate for the Python consumers.

Python twin of `ci-hub/lib/qualifying_receipt.rs`. "Does this ledger row qualify
as a full green?" was answered by five independent inline certifiers across three
languages; each was its own floor and each drifted (see task
`one-shared-qualifying-receipt-predicate-five-consumers-bypass-the-registry`,
source sweep `ai_docs/2026-08-04-floor-consumer-sweep.md`). The fix is ONE data
artifact -- `ci-hub/validate/qualifying-receipt.json` -- that every consumer
READS rather than restating inline. Because the consumers span Rust, Python, and
jq the shared thing cannot be one function; it is one DATUM each language loads.
`row_qualifies` here mirrors `row_qualifies` in the Rust module clause for clause
so the two engines never disagree, and both honour the SAME env override so a
mutation of the datum moves every consumer's answer at once.

Resolution order (mirrors the Rust module):
  1. `$QUALIFYING_RECEIPT_PREDICATE` -- an explicit override path (the mutation
     test points every consumer here; NEVER at the live file).
  2. the on-disk `ci-hub/validate/qualifying-receipt.json` beside this file.
A malformed override / on-disk file is a deploy defect and raises (loud), never a
silent fallback that would mask the very drift this module exists to prevent.

COMPLETENESS IS A COVERAGE QUESTION, NOT A COUNT QUESTION: a count-capable
receipt is held to per-node coverage, and `executed_tests` survives ONLY as the
zero-execution floor. See the JSON `_completeness_is_coverage_not_count` note.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PREDICATE_ENV = "QUALIFYING_RECEIPT_PREDICATE"
# The canonical file lives beside this module's `validate/` sibling. The literal
# repo-relative path lives in the Rust module's PREDICATE_REL; here we resolve
# against this file so a Python consumer needs no repo-root argument.
PREDICATE_REL = "validate/qualifying-receipt.json"

_ACTIVE: dict[str, Any] | None = None


def predicate_path() -> Path:
    """On-disk location of the canonical predicate (beside this module)."""
    return Path(__file__).resolve().parent / PREDICATE_REL


def load() -> dict[str, Any]:
    """Load the predicate honouring the env override, then the on-disk file.

    A malformed / unreadable source raises RuntimeError -- a landing consumer
    must not run on a guessed predicate.
    """
    override = os.environ.get(PREDICATE_ENV)
    path = Path(override) if override else predicate_path()
    try:
        text = path.read_text()
    except OSError as error:
        raise RuntimeError(f"{path}: cannot read qualifying predicate: {error}")
    try:
        pred = json.loads(text)
    except ValueError as error:
        raise RuntimeError(f"{path}: malformed qualifying predicate: {error}")
    # Fail loud if a required key is missing -- a partial predicate would let a
    # consumer become quietly more lenient than its peers.
    for key in ("counts_schema", "require", "coverage"):
        if key not in pred:
            raise RuntimeError(f"{path}: qualifying predicate missing key '{key}'")
    for key in (
        "commit_anchored",
        "tree_dirty",
        "profile",
        "selection_mode",
        "result",
        "failures_max",
        "executed_tests_min",
    ):
        if key not in pred["require"]:
            raise RuntimeError(f"{path}: qualifying predicate missing require.{key}")
    for key in ("applies_at_schema_min", "per_node"):
        if key not in pred["coverage"]:
            raise RuntimeError(f"{path}: qualifying predicate missing coverage.{key}")
    return pred


def active() -> dict[str, Any]:
    """Process-wide cached predicate. Resolved once; re-read only across runs."""
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = load()
    return _ACTIVE


def coverage_satisfied(cov: Any) -> bool:
    """A per-node coverage obligation is SATISFIED iff the run planned at least
    one test-bearing DAG node AND no planned test node was inert or absent. The
    NAMES travel with the receipt so this is re-derived without re-reading a log
    (Proxy Binding). Mirrors the Rust `coverage_satisfied`."""
    return (
        isinstance(cov, dict)
        and cov.get("planned_test_nodes", 0) > 0
        and cov.get("zero_executed_nodes") == []
        and cov.get("absent_nodes") == []
    )


def row_qualifies(row: dict[str, Any], sha: str, pred: dict[str, Any]) -> bool:
    """THE qualifying-receipt predicate, mirroring the Rust `row_qualifies`
    clause for clause. Completeness for a count-capable receipt is decided by
    per-node COVERAGE, not the executed_tests count."""
    req = pred["require"]
    if not (
        row.get("commit") == sha
        and row.get("commit_anchored") is req["commit_anchored"]
        and row.get("tree_dirty") is req["tree_dirty"]
        and row.get("selection_mode") == req["selection_mode"]
        and row.get("profile") == req["profile"]
        and row.get("result") == req["result"]
    ):
        return False
    # `pass` with a positive failure count is malformed; an absent count on a
    # pass row is treated as zero (old rows predate the field).
    if (row.get("failures") or 0) > req["failures_max"]:
        return False
    # A demonstrated zero-test run is never a full green, at any schema.
    if row.get("executed_tests") == 0:
        return False
    if pred.get("gate_filtered_tests") and row.get("filtered_tests") != 0:
        return False
    schema = row.get("schema_version") or 0
    count_capable = schema >= pred["counts_schema"]
    counts_present = (
        row.get("executed_tests") is not None and row.get("filtered_tests") is not None
    )
    exec_val = row.get("executed_tests")
    # The surviving zero-execution floor; NOT a completeness discriminator.
    executed_ok = isinstance(exec_val, int) and exec_val >= req["executed_tests_min"]
    cov = pred["coverage"]
    if count_capable:
        coverage_ok = (
            not cov["per_node"]
            or schema < cov["applies_at_schema_min"]
            or coverage_satisfied(row.get("coverage"))
        )
        return executed_ok and coverage_ok
    if counts_present:
        # Old-schema writer that carried counts but predates per-node coverage:
        # hold it to the strongest thing it can prove -- nonzero execution.
        return executed_ok
    # Neither count present: an uncounted receipt is UNVERIFIED, not green.
    return False

#!/usr/bin/env python3
"""The SINGLE qualifying-receipt predicate for the Python consumers.

Python twin of `ci-hub/lib/qualifying_receipt.rs`. "Does this ledger row qualify
as a full green?" was answered by independent inline certifiers across three
languages; the original sweep found five and follow-up review found two more in
anchor selection and receipt finalization. Each was its own floor and drifted
(see task
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

import argparse
import json
import os
import re
import sys
from enum import Enum
from pathlib import Path
from typing import Any


U64_MAX = (1 << 64) - 1

PREDICATE_ENV = "QUALIFYING_RECEIPT_PREDICATE"
# The canonical file lives beside this module's `validate/` sibling. The literal
# repo-relative path lives in the Rust module's PREDICATE_REL; here we resolve
# against this file so a Python consumer needs no repo-root argument.
PREDICATE_REL = "validate/qualifying-receipt.json"

_ACTIVE: dict[str, Any] | None = None


# The green CLASS (hard vs inherited/soft) is derived from the row's provenance by
# ci-hub/validate/green_class.py. It is imported rather than restated: a second copy
# of the derivation is exactly the drift this shared predicate exists to remove.
sys.path.insert(0, str(Path(__file__).resolve().parent / "validate"))
import green_class as _green_class  # noqa: E402


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


class CoverageVerdict(Enum):
    """Typed outcome of the per-node coverage obligation."""

    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNAVAILABLE = "unavailable"


def coverage_verdict(cov: Any) -> CoverageVerdict:
    """Decide coverage from the receipt fields alone.

    Both failure lists must be present as lists. An omitted or malformed list is
    UNKNOWN (`UNAVAILABLE`), never an empty-list success. A reported nonempty
    list is a distinct `UNSATISFIED` outcome. Mirrors the Rust
    `coverage_verdict` authority.
    """
    if not isinstance(cov, dict):
        return CoverageVerdict.UNAVAILABLE
    planned = cov.get("planned_test_nodes")
    zero_executed = cov.get("zero_executed_nodes")
    absent = cov.get("absent_nodes")
    if (
        not isinstance(planned, int)
        or isinstance(planned, bool)
        or not 0 < planned <= U64_MAX
        or not isinstance(zero_executed, list)
        or not isinstance(absent, list)
        or any(not isinstance(name, str) for name in zero_executed)
        or any(not isinstance(name, str) for name in absent)
    ):
        return CoverageVerdict.UNAVAILABLE
    if zero_executed or absent:
        return CoverageVerdict.UNSATISFIED
    return CoverageVerdict.SATISFIED


def coverage_satisfied(cov: Any) -> bool:
    """True only for an explicitly reported, satisfied coverage obligation."""
    return coverage_verdict(cov) is CoverageVerdict.SATISFIED


def _row_qualification_without_class(
    row: dict[str, Any], sha: str, pred: dict[str, Any]
) -> tuple[bool, str]:
    """THE qualifying-receipt predicate, mirroring the Rust `row_qualifies`
    clause for clause. Completeness for a count-capable receipt is decided by
    per-node COVERAGE, not the executed_tests count. The reason names the first
    failing clause so downstream consumers do not need a diagnostic copy."""
    req = pred["require"]
    if row.get("commit") in (None, "", "unknown"):
        return False, "no-commit"
    if row.get("commit") != sha:
        return False, f"commit={row.get('commit')!r}"
    if row.get("commit_anchored") is not req["commit_anchored"]:
        return False, "commit_anchored"
    if row.get("tree_dirty") is not req["tree_dirty"]:
        return False, "tree_dirty"
    if row.get("profile") != req["profile"]:
        return False, f"profile={row.get('profile')!r}"
    if row.get("selection_mode") != req["selection_mode"]:
        return False, f"selection_mode={row.get('selection_mode')!r}(1-hop)"
    if row.get("result") != req["result"]:
        return False, f"result={row.get('result')!r}"
    # `pass` with a positive failure count is malformed; an absent count on a
    # pass row is treated as zero (old rows predate the field).
    if (row.get("failures") or 0) > req["failures_max"]:
        return False, f"failures={row.get('failures')}"
    # A demonstrated zero-test run is never a full green, at any schema.
    if row.get("executed_tests") == 0:
        return False, "executed_tests==0"
    if pred.get("gate_filtered_tests") and row.get("filtered_tests") != 0:
        return False, "filtered_tests!=0"
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
        if not executed_ok:
            return False, "count-capable receipt missing executed_tests"
        if cov["per_node"] and schema >= cov["applies_at_schema_min"]:
            verdict = coverage_verdict(row.get("coverage"))
            if verdict is not CoverageVerdict.SATISFIED:
                return False, f"count-capable receipt coverage {verdict.value}"
        return True, "qualifies"
    if counts_present:
        # Old-schema writer that carried counts but predates per-node coverage:
        # hold it to the strongest thing it can prove -- nonzero execution.
        if executed_ok:
            return True, "qualifies"
    # Neither count present: an uncounted receipt is UNVERIFIED, not green.
    return False, "pre-count receipt cannot prove nonzero execution"


def _row_qualifies_without_class(
    row: dict[str, Any], sha: str, pred: dict[str, Any]
) -> bool:
    """Compatibility boolean wrapper around the canonical diagnostic result."""
    return _row_qualification_without_class(row, sha, pred)[0]


def green_class_of(row: dict[str, Any]) -> str:
    """The row's derived green class. Delegates; never re-derives."""
    return _green_class.derive_class(row)[0]


def row_qualification(
    row: dict[str, Any], sha: str, pred: dict[str, Any]
) -> tuple[bool, str]:
    """THE qualifying-receipt predicate, plus the green-CLASS clause.

    The class clause is applied LAST and can only NARROW: a row that already
    failed the value clauses stays refused, and a row that passed them must
    additionally be of a class the predicate accepts. Ordering matters -- putting
    it first would let a class check mask a value failure and change which reason
    a refusal reports.

    Behaviour is unchanged for every row that exists today: `accepts_green_class`
    defaults to ["hard"], and a row with no `validated_head_sha` derives its class
    as hard (measured: 585/585 live ledger rows).
    """
    value_ok, reason = _row_qualification_without_class(row, sha, pred)
    if not value_ok:
        return False, reason
    green_class, class_reason = _green_class.derive_class(row)
    if green_class not in _green_class.accepted_classes(pred):
        return False, f"green_class={green_class!r}: {class_reason}"
    return True, "qualifies"


def row_qualifies(row: dict[str, Any], sha: str, pred: dict[str, Any]) -> bool:
    """Boolean compatibility wrapper around :func:`row_qualification`."""
    return row_qualification(row, sha, pred)[0]


def main(argv: list[str] | None = None) -> int:
    """Semantic row-verifier CLI used by non-Python authority consumers."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        print("qualifying-receipt: --sha must be 40 lowercase hex", file=sys.stderr)
        return 2
    try:
        row = json.load(sys.stdin)
        if not isinstance(row, dict):
            raise ValueError("ledger row is not an object")
        pred = active()
        green_class, reason = _green_class.derive_class(row)
        accepted = row_qualifies(row, args.sha, pred)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"qualifying-receipt: {error}", file=sys.stderr)
        return 2
    report = {
        "schema_version": 1,
        "sha": args.sha,
        "accepted": accepted,
        "green_class": green_class,
        "reason": reason,
        "accepts_green_class": _green_class.accepted_classes(pred),
    }
    if args.json:
        print(json.dumps(report, sort_keys=True))
    elif accepted:
        print(f"QUALIFIED {args.sha} class={green_class}")
    else:
        print(f"REFUSED {args.sha} class={green_class}: {reason}")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())

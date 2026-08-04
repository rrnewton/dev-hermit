#!/usr/bin/env python3
"""Emit the canonical qualified PASS view of the validate ledger.

This is the guard for ad-hoc derived views. Two invariants are non-negotiable:

1. Order by event time (``finished_at``), never append/file position.
2. Drop incomplete, aborted, and zero-executed rows before bucketing or timing.

A row qualifies only when it records ``result == "pass"``, a positive executed
test count, a parseable completion time, and matching positive
``gates_run == gates_expected``. Missing evidence fails closed. Callers needing
failure taxonomy must use ``flake_class.effective_result``; this accessor is the
canonical population for green timing/concurrency analysis only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
from typing import Iterable, Mapping

from flake_class import gate_counts


DEFAULT_LEDGER = Path(__file__).resolve().parents[2] / "ignored" / "validate-run-ledger.jsonl"


def event_time(row: Mapping[str, object]) -> dt.datetime | None:
    value = row.get("finished_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def is_qualified(row: Mapping[str, object]) -> bool:
    """Return whether ``row`` is a complete, nonempty PASS measurement."""
    # Use the same schema-aware resolver as the failure taxonomy. Current
    # schema-3+ full rows record `checks` but not yet `gates_expected`; their
    # five-gate contract is known. Older/ambiguous rows remain unqualified.
    ran, expected = gate_counts(dict(row))
    executed = row.get("executed_tests")
    return (
        row.get("result") == "pass"
        and isinstance(executed, int)
        and not isinstance(executed, bool)
        and executed > 0
        and isinstance(ran, int)
        and isinstance(expected, int)
        and expected > 0
        and ran == expected
        and event_time(row) is not None
    )


def qualified_rows(rows: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    """Return qualified rows sorted by event time, independent of file order."""
    selected = [dict(row) for row in rows if is_qualified(row)]
    return sorted(
        selected,
        key=lambda row: (
            event_time(row),
            str(row.get("commit") or ""),
            str(row.get("slot") or ""),
            str(row.get("log_file") or ""),
        ),
    )


def load_rows(path: Path) -> tuple[list[dict[str, object]], int]:
    rows: list[dict[str, object]] = []
    malformed = 0
    with path.open(errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(value, dict):
                rows.append(value)
            else:
                malformed += 1
    return rows, malformed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args(argv)
    try:
        rows, malformed = load_rows(args.ledger)
    except OSError as error:
        print(f"qualified-rows: cannot read {args.ledger}: {error}", file=sys.stderr)
        return 2
    selected = qualified_rows(rows)
    for row in selected:
        print(json.dumps(row, separators=(",", ":"), sort_keys=True))
    print(
        f"qualified-rows: {len(selected)}/{len(rows)} qualified; "
        f"malformed={malformed}; sorted=finished_at",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

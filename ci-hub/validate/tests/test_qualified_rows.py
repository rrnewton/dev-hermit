#!/usr/bin/env python3
"""Mutation brackets for the canonical qualified ledger view."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qualified_rows as qr


def _row(name: str, finished: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "commit": name,
        "finished_at": finished,
        "result": "pass",
        "executed_tests": 10,
        "gates_run": 5,
        "gates_expected": 5,
    }
    row.update(overrides)
    return row


def test_qualified_rows_sort_by_event_time_not_file_position() -> None:
    late = _row("late", "2026-08-04T12:00:00Z")
    early = _row("early", "2026-08-04T10:00:00Z")
    assert [row["commit"] for row in qr.qualified_rows([late, early])] == [
        "early",
        "late",
    ]


def test_truncated_and_zero_executed_rows_do_not_qualify() -> None:
    truncated = _row("truncated", "2026-08-04T10:00:00Z", gates_run=2)
    inert = _row("inert", "2026-08-04T10:01:00Z", executed_tests=0)
    assert qr.qualified_rows([truncated, inert]) == []


def test_missing_conditions_fail_closed() -> None:
    missing_counts = _row("missing-counts", "2026-08-04T10:00:00Z")
    missing_counts.pop("gates_expected")
    missing_time = _row("missing-time", "")
    assert qr.qualified_rows([missing_counts, missing_time]) == []


def test_current_full_schema_derives_five_gate_contract() -> None:
    current = _row("current", "2026-08-04T10:00:00Z", schema_version=5)
    current["profile"] = "full"
    current["checks"] = current.pop("gates_run")
    current.pop("gates_expected")
    assert qr.qualified_rows([current]) == [current]


def test_n_equals_three_legitimate_passes_remain_qualified() -> None:
    rows = [
        _row("a", "2026-08-04T10:00:00Z"),
        _row("b", "2026-08-04T10:01:00Z"),
        _row("c", "2026-08-04T10:02:00Z"),
    ]
    assert qr.qualified_rows(rows) == rows

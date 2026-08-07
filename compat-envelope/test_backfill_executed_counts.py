#!/usr/bin/env python3
"""Tests for the executed-count backfill.

The risk in a backfill is not that it fails loudly -- it is that it quietly
invents a number and makes an unqualified record look qualified. Most of these
tests assert that it does NOT do something.
"""

from __future__ import annotations

import csv
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backfill_executed_counts import (  # noqa: E402
    COUNT_FIELDS,
    UNQUALIFIED,
    apply_to_text,
    plan,
)

HEADER = ["run_id", "backend", "outcome", "stdout_parity",
          "selected_count", "executed_count", "evidence_count"]


def make(rows, header=None) -> str:
    header = header or HEADER
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in header})
    return buf.getvalue()


def row(run="r1", outcome="pass", parity="", **kw):
    d = {"run_id": run, "backend": "dbi", "outcome": outcome, "stdout_parity": parity}
    d.update(kw)
    return d


def read(text):
    return list(csv.DictReader(io.StringIO(text)))


class DerivationTests(unittest.TestCase):
    def test_executed_excludes_unavailable_and_blank(self):
        text = make([row(), row(outcome="unavailable"), row(outcome=""), row(outcome="diverge")])
        out, plans, _ = apply_to_text(text)
        p = plans["r1"]
        self.assertEqual((p.selected, p.executed), (4, 2))

    def test_diverge_counts_as_executed(self):
        """A cell that ran and failed still RAN. Executed is not 'passed'."""
        _, plans, _ = apply_to_text(make([row(outcome="diverge"), row(outcome="timeout")]))
        self.assertEqual(plans["r1"].executed, 2)

    def test_evidence_requires_a_qualified_parity_value(self):
        text = make([row(parity="1"), row(parity=""), row(parity="0")])
        _, plans, _ = apply_to_text(text)
        self.assertEqual(plans["r1"].evidence, 2)

    def test_counts_are_run_level_not_per_row(self):
        """Two runs in one file must get their own counts."""
        text = make([row(run="a"), row(run="a"), row(run="b", outcome="unavailable")])
        out, plans, _ = apply_to_text(text)
        self.assertEqual((plans["a"].selected, plans["a"].executed), (2, 2))
        self.assertEqual((plans["b"].selected, plans["b"].executed), (1, 0))
        rows = read(out)
        self.assertEqual([r["executed_count"] for r in rows], ["2", "2", "0"])

    def test_zero_executed_is_written_as_0_not_blank(self):
        out, _, _ = apply_to_text(make([row(outcome="unavailable")]))
        self.assertEqual(read(out)[0]["executed_count"], "0")


class NonInventionTests(unittest.TestCase):
    """The backfill must never upgrade an unqualified record."""

    def test_existing_value_is_preserved_never_overwritten(self):
        text = make([row(executed_count="999"), row()])
        out, _, stats = apply_to_text(text)
        rows = read(out)
        self.assertEqual(rows[0]["executed_count"], "999")
        self.assertGreaterEqual(stats["preserved"], 1)

    def test_non_derivable_run_is_marked_unqualified_not_blank(self):
        header = [h for h in HEADER if h != "outcome"]
        text = make([{"run_id": "r1", "backend": "dbi"}], header=header)
        out, plans, stats = apply_to_text(text)
        self.assertFalse(plans["r1"].derivable)
        self.assertEqual(read(out)[0]["executed_count"], UNQUALIFIED)
        self.assertEqual(stats["unqualified"], 3)
        self.assertNotEqual(read(out)[0]["executed_count"], "")

    def test_missing_columns_are_not_invented(self):
        header = ["run_id", "backend", "outcome"]
        text = make([row()], header=header)
        out, _, stats = apply_to_text(text)
        self.assertEqual(stats["missing_columns"], len(COUNT_FIELDS))
        self.assertEqual(out, text, "text must be returned unchanged when the columns are absent")

    def test_empty_scorecard_is_refused(self):
        with self.assertRaises(ValueError):
            apply_to_text(make([]))

    def test_header_and_row_count_are_preserved(self):
        text = make([row(), row(run="b")])
        out, _, _ = apply_to_text(text)
        self.assertEqual(list(csv.DictReader(io.StringIO(out)).fieldnames), HEADER)
        self.assertEqual(len(read(out)), 2)


class PublishedShapeTests(unittest.TestCase):
    """Regression against the real published values (origin/main, 2026-08-07)."""

    def test_canonical_release_run_shape(self):
        """46 selected, 39 executed -- 7 unavailable. Both numbers must survive."""
        rows = [row(run="canonical-release-ptrace-dbi") for _ in range(39)]
        rows += [row(run="canonical-release-ptrace-dbi", outcome="unavailable") for _ in range(7)]
        _, plans, _ = apply_to_text(make(rows))
        p = plans["canonical-release-ptrace-dbi"]
        self.assertEqual((p.selected, p.executed), (46, 39))

    def test_all_evidence_demoted_gives_zero_not_blank(self):
        """Legacy parity was demoted, so qualified evidence is 0 -- explicitly."""
        out, plans, _ = apply_to_text(make([row(parity="") for _ in range(12)]))
        self.assertEqual(plans["r1"].evidence, 0)
        self.assertEqual(read(out)[0]["evidence_count"], "0")


if __name__ == "__main__":
    unittest.main(verbosity=2)

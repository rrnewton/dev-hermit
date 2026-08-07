#!/usr/bin/env python3
"""Bracketed fixtures for the label taxonomy validator.

Every fixture is an in-memory / tmpdir sqlite database shaped like `tasks_v`.
Nothing reads or writes the real TaskGraph.

Both directions are exercised deliberately. A validator that refuses everything
would pass every "missing/conflicting is refused" test while being useless, so
each negative is paired with a positive that must stay silent.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "label_taxonomy.py"
SPEC = importlib.util.spec_from_file_location("label_taxonomy", MODULE)
assert SPEC and SPEC.loader
tax = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tax)


def make_db(path: Path, rows: list[tuple[str, str, str, list[str]]]) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE tasks_v (local_id TEXT, status TEXT, priority TEXT, tags TEXT)"
    )
    con.executemany(
        "INSERT INTO tasks_v VALUES (?,?,?,?)",
        [(lid, st, pr, json.dumps(tags)) for lid, st, pr, tags in rows],
    )
    con.commit()
    con.close()


class TaxonomyTest(unittest.TestCase):
    def run_on(self, rows, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            make_db(db, rows)
            return tax.main(["--db", str(db), *extra])

    # ---- positives: correctly-labelled work must stay silent -------------

    def test_positive_one_of_each_axis_passes(self):
        rows = [("t1", "OPEN", "P0", ["release:0.3", "active-implementation"]),
                ("t2", "BACKLOG", "P2", ["backend:kvm", "research"])]
        self.assertEqual(0, self.run_on(rows, "--gate", "all"))

    def test_positive_backend_family_counts_as_one_workstream(self):
        """`backend:<name>` is a family, so a new backend needs no code change."""
        rows = [("t1", "OPEN", "P0", ["backend:sabre", "review"])]
        self.assertEqual(0, self.run_on(rows, "--gate", "all"))
        ws, lc = tax.axes({"backend:e9patch", "landing"})
        self.assertEqual((["backend:e9patch"], ["landing"]), (ws, lc))

    def test_positive_unrelated_tags_are_ignored_not_counted(self):
        """Existing tags like `source:gchat` must not be read as an axis."""
        rows = [("t1", "OPEN", "P1",
                 ["source:gchat", "integrity", "main-health", "landing"])]
        self.assertEqual(0, self.run_on(rows, "--gate", "all"))

    # ---- negatives: each failure mode refused, and NAMED ------------------

    def test_negative_missing_workstream_is_refused(self):
        rows = [("t1", "OPEN", "P0", ["active-implementation"])]
        self.assertEqual(1, self.run_on(rows, "--gate", "all"))
        r = tax.classify_row("t1", "P0", {"active-implementation"})
        self.assertIn("missing-workstream", r["problems"])

    def test_negative_missing_lifecycle_is_refused(self):
        rows = [("t1", "OPEN", "P0", ["release:0.3"])]
        self.assertEqual(1, self.run_on(rows, "--gate", "all"))
        r = tax.classify_row("t1", "P0", {"release:0.3"})
        self.assertIn("missing-lifecycle", r["problems"])

    def test_negative_conflicting_workstream_is_refused_and_names_both(self):
        """A count is not actionable; the operator needs to know WHICH two."""
        r = tax.classify_row("t1", "P0", {"release:0.3", "strictness", "review"})
        self.assertFalse(r["ok"])
        problem = next(p for p in r["problems"] if p.startswith("conflicting-workstream"))
        self.assertIn("release:0.3", problem)
        self.assertIn("strictness", problem)
        self.assertEqual(1, self.run_on(
            [("t1", "OPEN", "P0", ["release:0.3", "strictness", "review"])], "--gate", "all"))

    def test_negative_conflicting_lifecycle_is_refused(self):
        r = tax.classify_row("t1", "OPEN", {"operations", "research", "review"})
        self.assertFalse(r["ok"])
        self.assertTrue(any(p.startswith("conflicting-lifecycle") for p in r["problems"]))

    # ---- the gate, both directions ---------------------------------------

    def test_gate_p01_enforces_priority_work_but_only_reports_the_tail(self):
        """The introduction path.

        With ~90% of the real graph unlabelled, gating on everything would be
        red for weeks and get muted -- the same failure mode as an alarm that
        can never go green. So P0/P1 fails the exit code while the backlog is
        reported. Both halves are asserted: the P2 offender must NOT flip the
        exit code, and the P0 offender MUST.
        """
        tail_only = [("t2", "BACKLOG", "P2", [])]
        self.assertEqual(0, self.run_on(tail_only, "--gate", "p01"),
                         "an unlabelled P2 must not fail the introduction gate")
        self.assertEqual(1, self.run_on(tail_only, "--gate", "all"),
                         "...but --gate all must still see it")

        with_p0 = [("t1", "OPEN", "P0", []), ("t2", "BACKLOG", "P2", [])]
        self.assertEqual(1, self.run_on(with_p0, "--gate", "p01"),
                         "an unlabelled P0 must fail even under the introduction gate")

    def test_priority_ordering_puts_release_and_main_health_first(self):
        order = tax.WORKSTREAM_ORDER
        self.assertLess(order["release:0.3"], order["main-health"])
        self.assertLess(order["main-health"], order["strictness"])
        for other in ("operations", "owner-decision"):
            self.assertNotIn(other, order)  # sorts last, but is not an error

    def test_counts_reconcile_to_the_denominator(self):
        """ok + violations must equal the denominator, or the report is lying."""
        rows = [("t1", "OPEN", "P0", ["release:0.3", "review"]),
                ("t2", "OPEN", "P1", ["release:0.3"]),
                ("t3", "BACKLOG", "P2", [])]
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            make_db(db, rows)
            parsed, agg = tax.load(db)
        self.assertEqual(3, len(parsed))
        self.assertEqual(3, agg, "cursor walk and aggregate must agree in one snapshot")
        ok = sum(1 for r in parsed if r["ok"])
        self.assertEqual(len(parsed), ok + sum(1 for r in parsed if not r["ok"]))
        self.assertEqual(1, ok)

    def test_malformed_tag_blob_does_not_crash_the_validator(self):
        """A comma string instead of JSON must degrade, not explode."""
        self.assertEqual({"a", "b"}, tax.parse_tags("a, b"))
        self.assertEqual(set(), tax.parse_tags(None))
        self.assertEqual({"release:0.3"}, tax.parse_tags('["release:0.3"]'))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""The spot-check cadence must AGE OUT, not merely record a date.

A cadence that never flags anything stale is decorative: it turns a one-time
measurement into a permanent green. So every rule here is bracketed both ways --
the fresh case must COUNT, and the expired case must be REFUSED.

The NEVER/STALE split is tested explicitly. They are both non-counting, but they
are different claims: NEVER means no measurement ever existed, STALE means one
existed and expired. Collapsing them would let "we have never checked this" read
as "our check is a bit old."
"""

from __future__ import annotations

import datetime as _dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "spot_check_cadence", Path(__file__).resolve().parents[1] / "spot-check-cadence.py"
)
scc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scc)

NOW = _dt.datetime(2026, 8, 7, 12, 0, 0, tzinfo=_dt.timezone.utc)


def days_ago(n: float) -> str:
    return (NOW - _dt.timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


class AgeState(unittest.TestCase):
    def test_fresh_counts(self):
        for d in (0, 1, 7, 13.9):
            with self.subTest(days=d):
                self.assertEqual(scc.age_state(days_ago(d), NOW)[0], scc.CURRENT)

    def test_expired_is_refused(self):
        """The whole point: past the cadence it must stop counting."""
        for d in (14.1, 15, 30, 365):
            with self.subTest(days=d):
                self.assertEqual(scc.age_state(days_ago(d), NOW)[0], scc.STALE)

    def test_boundary_is_inclusive_and_not_off_by_one(self):
        self.assertEqual(scc.age_state(days_ago(14), NOW)[0], scc.CURRENT)
        self.assertEqual(scc.age_state(days_ago(14.001), NOW)[0], scc.STALE)

    def test_never_is_distinct_from_stale(self):
        """A blank date is NEVER, not STALE, and never CURRENT."""
        for blank in ("", "   ", None):
            with self.subTest(value=blank):
                state, age = scc.age_state(blank, NOW)
                self.assertEqual(state, scc.NEVER)
                self.assertIsNone(age)

    def test_blank_is_not_read_as_the_epoch(self):
        """Guards the classic bug: '' parsed as 0 would be maximally stale, or
        with a shifted clock could be read as fresh. It must be NEVER either way."""
        for clock in (_dt.datetime(1971, 1, 1, tzinfo=_dt.timezone.utc), NOW):
            with self.subTest(clock=clock.year):
                self.assertEqual(scc.age_state("", clock)[0], scc.NEVER)

    def test_unparseable_date_is_never_not_current(self):
        """Fail closed: garbage must not be promoted to a passing state."""
        for bad in ("yesterday", "2026-13-45", "NaN", "0"):
            with self.subTest(value=bad):
                self.assertEqual(scc.age_state(bad, NOW)[0], scc.NEVER)

    def test_cadence_is_a_parameter_not_a_constant(self):
        ts = days_ago(20)
        self.assertEqual(scc.age_state(ts, NOW, cadence_days=14)[0], scc.STALE)
        self.assertEqual(scc.age_state(ts, NOW, cadence_days=30)[0], scc.CURRENT)

    def test_age_is_reported_so_the_verdict_is_auditable(self):
        state, age = scc.age_state(days_ago(3), NOW)
        self.assertEqual(state, scc.CURRENT)
        self.assertAlmostEqual(age, 3.0, places=1)


class LargeCellSelection(unittest.TestCase):
    def _sc(self, rows) -> Path:
        import csv, tempfile
        p = Path(tempfile.mkdtemp()) / "sc.csv"
        cols = ["test_id", "test_mode", "backend", "outcome", "duration_ms"]
        with p.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        return p

    def test_timeouts_are_excluded_as_already_no_result(self):
        """Spot-checking a timeout cannot produce stack/heap evidence; counting
        it as a large cell would inflate the denominator with unmeasurable rows."""
        p = self._sc([
            {"test_id": "t1", "test_mode": "verify", "backend": "ptrace",
             "outcome": "timeout", "duration_ms": "120000"},
            {"test_id": "t2", "test_mode": "verify", "backend": "ptrace",
             "outcome": "pass", "duration_ms": "120000"},
        ])
        got = scc.large_cells(p)
        self.assertEqual([r["test_id"] for r in got], ["t2"])

    def test_short_cells_excluded_and_threshold_is_inclusive(self):
        p = self._sc([
            {"test_id": "short", "test_mode": "v", "backend": "ptrace",
             "outcome": "pass", "duration_ms": "4999"},
            {"test_id": "exact", "test_mode": "v", "backend": "ptrace",
             "outcome": "pass", "duration_ms": "5000"},
        ])
        self.assertEqual([r["test_id"] for r in scc.large_cells(p)], ["exact"])

    def test_non_numeric_duration_is_not_silently_large(self):
        p = self._sc([{"test_id": "x", "test_mode": "v", "backend": "ptrace",
                       "outcome": "pass", "duration_ms": ""}])
        self.assertEqual(scc.large_cells(p), [])

    def test_diverge_and_fail_are_still_spot_checkable(self):
        """They EXECUTED, so stack/heap evidence about them is meaningful."""
        p = self._sc([
            {"test_id": "d", "test_mode": "v", "backend": "ptrace",
             "outcome": "diverge", "duration_ms": "9000"},
            {"test_id": "f", "test_mode": "v", "backend": "ptrace",
             "outcome": "fail", "duration_ms": "9000"},
        ])
        self.assertEqual(len(scc.large_cells(p)), 2)


if __name__ == "__main__":
    unittest.main()

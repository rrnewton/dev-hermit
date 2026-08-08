#!/usr/bin/env python3
"""Tests for the ratchet regression gate.

Bracketed throughout: every refusal has a matching acceptance, so a gate that
simply refused everything would fail this file.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ratchet_gate import (  # noqa: E402
    ACCEPT_INCREASE,
    ACCEPT_LEVEL,
    ACCEPT_REBASELINE,
    REFUSE_MALFORMED,
    REFUSE_REGRESSION,
    REFUSE_RETREAT,
    SeriesError,
    evaluate,
    load_series,
)

GATE = Path(__file__).resolve().parent / "ratchet_gate.py"
SERIES_JSON = (Path(__file__).resolve().parents[1]
               / "ai_docs" / "measurements" / "qualified-green-ratchet-series.json")

D_STRICT = "b7e92321308d83fb9c36d78b5d32823ff7ce2e91"
D_WEAK = "3825d05d6e957bad8e9d0d866d217a8204709d8c"
D_NEW = "c" * 40


def series(points=None, defs=None):
    return {
        "series": "test",
        "definitions": defs if defs is not None else {
            D_WEAK: {"block": "D0", "strictness": 0},
            D_STRICT: {"block": "D1", "strictness": 1},
            D_NEW: {"block": "D2", "strictness": 2},
        },
        "points": points or [
            {"id": "D1.0", "date": "2026-08-07", "definition_sha": D_STRICT,
             "value": 0, "measured": 0, "total": 2284},
        ],
    }


def point(value, sha=D_STRICT, **kw):
    p = {"id": "new", "date": "2026-08-08", "definition_sha": sha,
         "value": value, "measured": value, "total": 2284}
    p.update(kw)
    return p


class SameDefinitionTests(unittest.TestCase):
    def test_increase_accepted(self):
        d = evaluate(series(), point(1))
        self.assertEqual(d.outcome, ACCEPT_INCREASE)
        self.assertTrue(d.accepted)

    def test_level_accepted(self):
        self.assertEqual(evaluate(series(), point(0)).outcome, ACCEPT_LEVEL)

    def test_decrease_with_no_definition_change_is_REFUSED(self):
        """The core rule."""
        base = series([{"id": "p", "date": "d", "definition_sha": D_STRICT,
                        "value": 5, "measured": 5, "total": 2284}])
        d = evaluate(base, point(4))
        self.assertEqual(d.outcome, REFUSE_REGRESSION)
        self.assertFalse(d.accepted)
        self.assertIn("UNCHANGED", d.detail)

    def test_one_below_is_still_a_regression(self):
        """No tolerance band: n-1 is a regression."""
        base = series([{"id": "p", "date": "d", "definition_sha": D_STRICT,
                        "value": 100, "measured": 100, "total": 2284}])
        self.assertEqual(evaluate(base, point(99)).outcome, REFUSE_REGRESSION)


class RebaselineTests(unittest.TestCase):
    def test_decrease_with_declared_definition_change_is_accepted_as_rebaseline(self):
        d = evaluate(series(), point(0, sha=D_NEW, rebaseline=True,
                                     rebaseline_reason="tightened to 5-signal comparison"))
        self.assertEqual(d.outcome, ACCEPT_REBASELINE)
        self.assertTrue(d.accepted)
        self.assertIn("RE-BASELINE", d.detail)

    def test_definition_change_without_declaring_rebaseline_is_refused(self):
        """A SHA differing must not silently authorise a drop."""
        d = evaluate(series(), point(0, sha=D_NEW))
        self.assertEqual(d.outcome, REFUSE_MALFORMED)
        self.assertIn("does not declare rebaseline", d.detail)

    def test_rebaseline_without_a_reason_is_refused(self):
        d = evaluate(series(), point(0, sha=D_NEW, rebaseline=True))
        self.assertEqual(d.outcome, REFUSE_MALFORMED)

    def test_rebaseline_is_logged_not_silent(self):
        d = evaluate(series(), point(0, sha=D_NEW, rebaseline=True,
                                     rebaseline_reason="tightened"))
        self.assertIn("no", d.detail.lower())
        self.assertIn("monotonicity is claimed across", d.detail)


class RetreatTests(unittest.TestCase):
    """The move the tightening exists to prevent."""

    def setUp(self):
        self.base = series([
            {"id": "D0.b", "date": "d", "definition_sha": D_WEAK,
             "value": 1837, "measured": 1837, "total": 2284},
            {"id": "D1.0", "date": "d", "definition_sha": D_STRICT,
             "value": 0, "measured": 0, "total": 2284},
        ])

    def test_returning_to_a_superseded_definition_is_refused(self):
        d = evaluate(self.base, point(1837, sha=D_WEAK, rebaseline=True,
                                      rebaseline_reason="the strict rule is too harsh"))
        self.assertEqual(d.outcome, REFUSE_RETREAT)
        self.assertFalse(d.accepted)
        self.assertIn("RETREAT", d.detail)

    def test_a_weaker_never_used_definition_is_also_refused(self):
        defs = {D_WEAK: {"strictness": 0}, D_STRICT: {"strictness": 1},
                "d" * 40: {"strictness": 0}}
        base = series([{"id": "p", "date": "d", "definition_sha": D_STRICT,
                        "value": 5, "measured": 5, "total": 10}], defs=defs)
        d = evaluate(base, point(9, sha="d" * 40, rebaseline=True,
                                 rebaseline_reason="different rule"))
        self.assertEqual(d.outcome, REFUSE_RETREAT)
        self.assertIn("WEAKER", d.detail)

    def test_a_stricter_new_definition_is_still_accepted(self):
        """Positive control: forward re-baselining must remain possible."""
        d = evaluate(self.base, point(0, sha=D_NEW, rebaseline=True,
                                      rebaseline_reason="tightened further"))
        self.assertEqual(d.outcome, ACCEPT_REBASELINE)


class MalformedTests(unittest.TestCase):
    def test_missing_definition_sha_is_refused(self):
        p = point(1)
        del p["definition_sha"]
        with self.assertRaises(SeriesError):
            evaluate(series(), p)

    def test_missing_measured_or_total_is_refused(self):
        for field in ("measured", "total"):
            p = point(1)
            del p[field]
            with self.subTest(field), self.assertRaises(SeriesError):
                evaluate(series(), p)

    def test_zero_total_is_refused(self):
        with self.assertRaises(SeriesError):
            evaluate(series(), point(0, total=0))

    def test_measured_exceeding_total_is_refused(self):
        with self.assertRaises(SeriesError):
            evaluate(series(), point(5, measured=99, total=10))

    def test_negative_value_is_refused(self):
        with self.assertRaises(SeriesError):
            evaluate(series(), point(-1))

    def test_empty_series_is_refused(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"points": []}, fh)
        with self.assertRaises(SeriesError):
            load_series(Path(fh.name))


class PublishedSeriesTests(unittest.TestCase):
    """The real series must load and must be self-consistent."""

    def setUp(self):
        if not SERIES_JSON.exists():
            self.skipTest("series json not present")
        self.series = load_series(SERIES_JSON)

    def test_it_loads(self):
        self.assertGreaterEqual(len(self.series["points"]), 3)

    def test_floor_point_is_zero_of_2284(self):
        floor = [p for p in self.series["points"] if p["id"] == "D1.0"][0]
        self.assertEqual(floor["value"], 0)
        self.assertEqual(floor["total"], 2284)
        self.assertEqual(floor["definition_sha"], D_STRICT)

    def test_the_recorded_1837_to_0_drop_is_a_declared_rebaseline(self):
        """The published history must itself pass the gate's own rule."""
        floor = [p for p in self.series["points"] if p["id"] == "D1.0"][0]
        self.assertTrue(floor.get("rebaseline"))
        self.assertTrue(floor.get("rebaseline_reason"))

    def test_replaying_the_published_history_through_the_gate_accepts_every_step(self):
        pts = self.series["points"]
        for i in range(1, len(pts)):
            prefix = {**self.series, "points": pts[:i]}
            d = evaluate(prefix, pts[i])
            with self.subTest(point=pts[i]["id"]):
                self.assertTrue(d.accepted, f"{pts[i]['id']}: {d.outcome} — {d.detail}")


class CliTests(unittest.TestCase):
    def _files(self, ser, pt):
        s = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(ser, s); s.close()
        p = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(pt, p); p.close()
        return s.name, p.name

    def _run(self, ser, pt):
        s, p = self._files(ser, pt)
        return subprocess.run([sys.executable, str(GATE), "--series", s, "--check-point", p],
                              capture_output=True, text=True)

    def test_exit_0_on_increase(self):
        self.assertEqual(self._run(series(), point(1)).returncode, 0)

    def test_exit_1_on_regression(self):
        base = series([{"id": "p", "date": "d", "definition_sha": D_STRICT,
                        "value": 5, "measured": 5, "total": 10}])
        r = self._run(base, point(4, total=10, measured=4))
        self.assertEqual(r.returncode, 1)
        self.assertIn(REFUSE_REGRESSION, r.stdout)

    def test_output_reports_both_values_and_both_definition_shas(self):
        r = self._run(series(), point(1))
        self.assertIn("previous :", r.stdout)
        self.assertIn("proposed :", r.stdout)
        self.assertIn(D_STRICT[:12], r.stdout)

    def test_exit_2_on_unusable_series(self):
        r = subprocess.run([sys.executable, str(GATE), "--series", "/nonexistent.json",
                            "--report"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Tests for the sparse-signal green-time model.

Both directions matter here. A carry-forward model can be made to report a
flattering number by carrying too eagerly, and a densification planner can be
made to look busy while never closing the gap it was pointed at. Each property
below is therefore paired with the case that would catch it cheating.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from timeline import (  # noqa: E402
    DEFAULT_MAX_GAP_SECONDS,
    HARD_GREEN,
    RED,
    SOFT_GREEN,
    UNKNOWN,
    Commit,
    build_timeline,
    find_gaps,
    plan_densification,
    plan_red_tightening,
    summarize,
    worst_of,
)

H = 3600


def commits(n, step=H, start=0):
    return [Commit(f"c{i:03d}", start + i * step) for i in range(n)]


class TestCarryForward(unittest.TestCase):
    def test_state_is_carried_between_signal_points(self):
        cs = commits(5)  # t = 0,1,2,3,4 h
        segs = build_timeline(cs, {"c000": HARD_GREEN, "c003": RED}, now=4 * H)
        # green from c000 until c003 flips it, then red to the end.
        self.assertEqual([(s.state, s.seconds) for s in segs],
                         [(HARD_GREEN, 3 * H), (RED, 1 * H)])

    def test_time_before_first_signal_is_unknown_not_green(self):
        """The bug that motivated the redesign, inverted: never invent green."""
        cs = commits(4)
        segs = build_timeline(cs, {"c002": HARD_GREEN}, now=3 * H)
        self.assertEqual(segs[0].state, UNKNOWN)
        self.assertEqual(segs[0].seconds, 2 * H)
        rep = summarize(cs, {"c002": HARD_GREEN}, now=3 * H)
        self.assertEqual(rep["unknown_lead_seconds"], 2 * H)
        # ... and the unknown lead must not inflate the green percentage.
        self.assertEqual(rep["green_pct"], 100.0)
        self.assertEqual(rep["attributable_seconds"], 1 * H)

    def test_repeated_same_state_is_not_a_flip_but_updates_provenance(self):
        cs = commits(4)
        sig = {"c000": HARD_GREEN, "c002": HARD_GREEN}
        segs = build_timeline(cs, sig, now=3 * H)
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0].source_sha, "c002")  # freshest evidence

    def test_no_signal_at_all_is_entirely_unknown(self):
        cs = commits(3)
        rep = summarize(cs, {}, now=2 * H)
        self.assertEqual(rep["green_pct"], 0.0)
        self.assertEqual(rep["attributable_seconds"], 0)
        self.assertEqual(rep["unknown_lead_seconds"], 2 * H)

    def test_worst_of_prefers_red(self):
        self.assertEqual(worst_of([HARD_GREEN, RED]), RED)
        self.assertEqual(worst_of([HARD_GREEN, SOFT_GREEN]), SOFT_GREEN)


class TestGaps(unittest.TestCase):
    def test_gap_is_bounded_by_its_signal_points(self):
        cs = commits(6)
        gaps = find_gaps(cs, {"c000": HARD_GREEN, "c004": RED}, now=5 * H)
        self.assertEqual(len(gaps), 2)  # c001..c003, and c005..now
        self.assertEqual(gaps[0].before_sha, "c000")
        self.assertEqual(gaps[0].after_sha, "c004")
        self.assertEqual(gaps[0].seconds, 3 * H)

    def test_trailing_gap_runs_to_now(self):
        cs = commits(3)
        gaps = find_gaps(cs, {"c000": HARD_GREEN}, now=10 * H)
        self.assertEqual(gaps[-1].end_ts, 10 * H)


class TestDensification(unittest.TestCase):
    def test_plan_closes_every_gap_below_target(self):
        """The property that matters: APPLYING the plan meets the target."""
        cs = commits(64, step=600)  # 10-minute commits over ~10.5h
        sig = {"c000": HARD_GREEN, "c063": HARD_GREEN}
        plan = plan_densification(cs, sig, now=63 * 600, max_gap_seconds=H, limit=None)
        self.assertTrue(plan)
        applied = dict(sig)
        for sha in plan:
            applied[sha] = HARD_GREEN
        rep = summarize(cs, applied, now=63 * 600, max_gap_seconds=H)
        self.assertTrue(rep["quality"]["meets_target"], rep["quality"])

    def test_plan_is_empty_when_target_already_met(self):
        """Guards the opposite failure: busywork when there is nothing to close."""
        cs = commits(3, step=60)
        sig = {c.sha: HARD_GREEN for c in cs}
        self.assertEqual(plan_densification(cs, sig, now=120, max_gap_seconds=H), [])

    def test_first_probe_halves_the_widest_gap(self):
        cs = commits(9)  # 8h span
        sig = {"c000": HARD_GREEN, "c008": HARD_GREEN}
        plan = plan_densification(cs, sig, now=8 * H, max_gap_seconds=H, limit=1)
        self.assertEqual(plan, ["c004"])  # the time midpoint, not the first hole

    def test_plan_prefix_targets_the_worst_gap_first(self):
        # One wide hole and one narrow hole; the wide one must be probed first.
        cs = [Commit("a", 0), Commit("b", 10 * H), Commit("c", 10 * H + 60),
              Commit("d", 10 * H + 120)]
        sig = {"a": HARD_GREEN, "b": HARD_GREEN, "d": HARD_GREEN}
        # The only unsignalled commit sits in the narrow hole, so a correct
        # planner reports nothing to do for the WIDE hole: it has no commits.
        plan = plan_densification(cs, sig, now=10 * H + 120, max_gap_seconds=H, limit=5)
        self.assertNotIn("a", plan)

    def test_limit_is_respected(self):
        cs = commits(64, step=600)
        sig = {"c000": HARD_GREEN, "c063": HARD_GREEN}
        self.assertEqual(
            len(plan_densification(cs, sig, now=63 * 600, max_gap_seconds=60, limit=3)), 3
        )


class TestRedTightening(unittest.TestCase):
    def test_red_yields_both_a_blame_probe_and_a_fix_probe(self):
        cs = commits(9)
        out = plan_red_tightening(cs, {"c004": RED}, now=8 * H)
        self.assertEqual(len(out), 1)
        row = out[0]
        self.assertEqual(row["red_sha"], "c004")
        self.assertIsNotNone(row["first_bad_probe"])  # walk EARLIER for blame
        self.assertIsNotNone(row["fix_probe"])        # walk LATER for the fix
        self.assertEqual(row["unsignalled_before"], 4)
        self.assertEqual(row["unsignalled_after"], 4)

    def test_green_commits_produce_no_tightening_work(self):
        cs = commits(5)
        self.assertEqual(plan_red_tightening(cs, {"c002": HARD_GREEN}, now=4 * H), [])

    def test_red_at_tip_has_no_fix_probe_yet(self):
        cs = commits(5)
        row = plan_red_tightening(cs, {"c004": RED}, now=4 * H)[0]
        self.assertIsNone(row["fix_probe"])


class TestSummaryHonesty(unittest.TestCase):
    def test_red_time_is_not_rounded_away(self):
        cs = commits(5)
        rep = summarize(cs, {"c000": HARD_GREEN, "c002": RED, "c004": HARD_GREEN}, now=4 * H)
        self.assertEqual(rep["red_pct"], 50.0)
        self.assertEqual(rep["green_pct"], 50.0)

    def test_soft_and_hard_green_are_reported_separately(self):
        cs = commits(5)
        rep = summarize(cs, {"c000": SOFT_GREEN, "c002": HARD_GREEN}, now=4 * H)
        self.assertEqual(rep["soft_green_pct"], 50.0)
        self.assertEqual(rep["hard_green_pct"], 50.0)
        self.assertEqual(rep["green_pct"], 100.0)

    def test_quality_block_flags_a_sparse_window(self):
        cs = commits(50)
        rep = summarize(cs, {"c000": HARD_GREEN}, now=49 * H,
                        max_gap_seconds=DEFAULT_MAX_GAP_SECONDS)
        self.assertFalse(rep["quality"]["meets_target"])
        self.assertGreater(rep["quality"]["max_gap_seconds"], DEFAULT_MAX_GAP_SECONDS)
        # A sparse window still yields a usable estimate -- that is the point.
        self.assertEqual(rep["green_pct"], 100.0)

    def test_unrecognised_signal_state_is_refused(self):
        with self.assertRaises(ValueError):
            build_timeline(commits(2), {"c000": "greenish"}, now=H)


if __name__ == "__main__":
    unittest.main(verbosity=2)

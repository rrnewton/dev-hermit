#!/usr/bin/env python3
"""BRACKETS for livelock_class — both sides of every gate, with real coordinates.

The planted cases are not invented numbers: the LIVELOCK case is the runner's own
recorded profile for `test.detcore_misc` @85626e18 (wall 600.013, user 495.162,
sys 104.824), and the CONTENDED case is `test.rr_suite_contract` as it appears in
the local step-profile corpus (wall 300.191, cpu 100.9). Using the measured
coordinates means a threshold change that would misclassify the real incidents
fails a test.

No network, no host inspection: `classify` is pure.
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(os.path.abspath(__file__)).parent
sys.path.insert(0, str(HERE))

import livelock_class as L  # noqa: E402


def call(**over):
    args = dict(step="test.x", wall_s=600.0, user_s=None, sys_s=None, timed_out=True)
    args.update(over)
    return L.classify(**args)


class TheRealIncidents(unittest.TestCase):
    """The two measured coordinates must land on opposite sides."""

    def test_confirmed_livelock_detcore_misc(self):
        """test.detcore_misc @85626e18: 600.013 wall / 599.986 CPU, killed rc=-9.
        Live evidence: 100% CPU in futex_ with a ptrace-stopped vfork child."""
        v = call(step="test.detcore_misc", wall_s=600.013, user_s=495.162,
                 sys_s=104.824, timed_out=True)
        self.assertEqual(v.verdict, L.LIVELOCK)
        self.assertAlmostEqual(v.cores_burned, 0.99995, places=4)
        self.assertIn("Re-dispatching", v.reason)

    def test_killed_but_blocked_rr_suite_contract(self):
        """The negative control from the same instrument: a real killed step that
        was WAITING. If this ever reads LIVELOCK the classifier is useless,
        because re-dispatch is the correct action here."""
        v = call(step="test.rr_suite_contract", wall_s=300.191, user_s=100.9,
                 sys_s=0.0, timed_out=True)
        self.assertEqual(v.verdict, L.CONTENDED_WAIT)
        self.assertLess(v.cores_burned, 0.4)

    def test_cpu_excluded_from_parent_getrusage_case(self):
        """detcore_misc @3d5b42ce: 601s wall, whole run recorded 7.47 CPU-s
        because the spinning child's CPU never reached the parent's getrusage.
        With that number this reads CONTENDED — which is exactly why the ledger
        top-level CPU must NOT be used as the input (see UnknownIsNotAGuess)."""
        v = call(wall_s=601.0, user_s=1.416, sys_s=6.054, timed_out=True)
        self.assertEqual(v.verdict, L.CONTENDED_WAIT)


class TimedOutIsMandatory(unittest.TestCase):
    """The ratio alone is NOT a livelock signature — the corpus median completed
    step already sits at ~0.919 cores."""

    def test_a_completed_step_at_full_core_is_not_flagged(self):
        v = call(wall_s=100.0, user_s=99.0, sys_s=1.0, timed_out=False)
        self.assertEqual(v.verdict, L.NOT_APPLICABLE)

    def test_a_completed_step_at_many_cores_is_not_flagged(self):
        """p90 of the corpus is 6.6 cores and the max is 127 — ordinary parallel
        work. Flagging that would make the detector useless."""
        v = call(wall_s=100.0, user_s=6000.0, sys_s=649.0, timed_out=False)
        self.assertEqual(v.verdict, L.NOT_APPLICABLE)

    def test_the_same_ratio_IS_flagged_once_it_is_killed(self):
        """Positive control for the conjunct: identical CPU profile, killed."""
        v = call(wall_s=100.0, user_s=99.0, sys_s=1.0, timed_out=True)
        self.assertEqual(v.verdict, L.LIVELOCK)


class MultiThreadedLivelock(unittest.TestCase):
    def test_n_spinning_threads_are_still_a_livelock(self):
        """'wall == CPU' only ever describes the SINGLE-threaded case; four
        spinning threads give ~4 cores and must not fall through."""
        v = call(wall_s=600.0, user_s=2400.0, sys_s=0.0, timed_out=True)
        self.assertEqual(v.verdict, L.LIVELOCK)
        self.assertAlmostEqual(v.cores_burned, 4.0, places=3)


class UnknownIsNotAGuess(unittest.TestCase):
    """The retroactive-impossibility finding, encoded."""

    def test_killed_with_no_cpu_data_is_UNKNOWN_not_contended(self):
        v = call(wall_s=600.0, user_s=None, sys_s=None, timed_out=True)
        self.assertEqual(v.verdict, L.UNKNOWN_NO_CPU)
        self.assertIn("absence of evidence", v.reason)

    def test_unknown_is_distinct_from_both_real_verdicts(self):
        self.assertNotIn(L.UNKNOWN_NO_CPU, (L.LIVELOCK, L.CONTENDED_WAIT))

    def test_partial_cpu_data_is_still_usable(self):
        """user_s present, sys_s absent: treat the missing half as zero rather
        than discarding the row — it can only UNDER-state cores burned, so it can
        only move a row toward CONTENDED, never falsely toward LIVELOCK."""
        v = call(wall_s=600.0, user_s=599.0, sys_s=None, timed_out=True)
        self.assertEqual(v.verdict, L.LIVELOCK)

    def test_zero_wall_is_unknown_not_a_division_error(self):
        self.assertEqual(call(wall_s=0.0, user_s=5.0, sys_s=1.0).verdict, L.UNKNOWN_NO_CPU)


class ThresholdIsStated(unittest.TestCase):
    def test_the_verdict_carries_the_threshold_it_used(self):
        self.assertEqual(call(wall_s=10.0, user_s=9.5, sys_s=0.0).threshold_cores, 0.90)

    def test_moving_the_threshold_moves_the_boundary_both_ways(self):
        args = dict(wall_s=100.0, user_s=50.0, sys_s=0.0, timed_out=True)
        self.assertEqual(L.classify(step="s", threshold_cores=0.4, **args).verdict, L.LIVELOCK)
        self.assertEqual(L.classify(step="s", threshold_cores=0.9, **args).verdict, L.CONTENDED_WAIT)

    def test_the_real_incidents_stay_separated_across_the_whole_gap(self):
        """Anti-brittleness: any threshold inside the measured gap must classify
        BOTH real cases correctly. If that stops holding, the gap closed and the
        threshold needs re-deriving rather than nudging."""
        for t in (0.40, 0.60, 0.75, 0.90, 0.98):
            live = L.classify(step="d", wall_s=600.013, user_s=495.162,
                              sys_s=104.824, timed_out=True, threshold_cores=t)
            wait = L.classify(step="r", wall_s=300.191, user_s=100.9, sys_s=0.0,
                              timed_out=True, threshold_cores=t)
            self.assertEqual(live.verdict, L.LIVELOCK, f"threshold {t}")
            self.assertEqual(wait.verdict, L.CONTENDED_WAIT, f"threshold {t}")


class CsvAndCli(unittest.TestCase):
    FIELDS = ["step", "elapsed_s", "user_s", "sys_s", "timed_out", "returncode"]

    def write(self, tmp, rows):
        path = Path(tmp) / "step_profiles_test.csv"
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=self.FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return str(path)

    def test_cli_exits_2_on_a_livelock_and_0_without_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = self.write(tmp, [dict(step="test.detcore_misc", elapsed_s=600.013,
                                         user_s=495.162, sys_s=104.824,
                                         timed_out="True", returncode=-9)])
            self.assertEqual(L.main(["--profiles", live, "--json"]), L.EXIT_LIVELOCK)
        with tempfile.TemporaryDirectory() as tmp:
            wait = self.write(tmp, [dict(step="test.rr_suite_contract", elapsed_s=300.191,
                                         user_s=100.9, sys_s=0.0,
                                         timed_out="True", returncode=-9)])
            self.assertEqual(L.main(["--profiles", wait, "--json"]), L.EXIT_OK)

    def test_completed_rows_do_not_trip_the_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = self.write(tmp, [dict(step="build.x", elapsed_s=100.0, user_s=6000.0,
                                       sys_s=649.0, timed_out="False", returncode=0)])
            self.assertEqual(L.main(["--profiles", ok, "--json"]), L.EXIT_OK)

    def test_unreadable_profile_is_an_error_not_a_silent_pass(self):
        self.assertEqual(L.main(["--profiles", "/nonexistent/x.csv", "--json"]), L.EXIT_ERROR)


class GateAdapter(unittest.TestCase):
    """The junction a timeout consumer calls: ledger gate + step profile."""

    PROFILE = {"test.detcore_misc": {"step": "test.detcore_misc", "elapsed_s": "600.013",
                                     "user_s": "495.162", "sys_s": "104.824",
                                     "timed_out": "True", "returncode": "-9"}}

    def test_ordinary_failure_is_not_classified_at_all(self):
        self.assertIsNone(L.classify_gate({"name": "x", "result": "fail",
                                           "returncode": 1}, self.PROFILE))

    def test_wall_kill_joined_to_its_profile_reads_LIVELOCK(self):
        v = L.classify_gate({"name": "portable:test.detcore_misc", "result": "timeout",
                             "real_seconds": 600.013}, self.PROFILE)
        self.assertEqual(v.verdict, L.LIVELOCK)
        self.assertEqual(v.step, "portable:test.detcore_misc",
                         "the gate's own name is reported, not the bare step")

    def test_wall_kill_with_NO_profile_is_UNKNOWN_not_contended(self):
        """The retroactive-impossibility encoded at the gate level: defaulting to
        CONTENDED here is what re-runs a confirmed livelock for a second budget."""
        v = L.classify_gate({"name": "test.detcore_misc", "result": "timeout",
                             "real_seconds": 600.0}, {})
        self.assertEqual(v.verdict, L.UNKNOWN_NO_CPU)
        self.assertIn("cgroup cpu.stat", v.reason)

    def test_each_wall_kill_exit_code_is_recognised(self):
        for rc in (124, 137, 143, -9, -15):
            self.assertTrue(L.gate_is_wall_kill({"returncode": rc}), rc)
        for rc in (0, 1, 101):
            self.assertFalse(L.gate_is_wall_kill({"returncode": rc}), rc)


if __name__ == "__main__":
    unittest.main(verbosity=2)

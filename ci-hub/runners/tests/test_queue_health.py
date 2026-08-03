#!/usr/bin/env python3
"""Tests for ci-hub/runners/queue_health.py (pure analysis; no network)."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import queue_health as qh

NOW = datetime(2026, 8, 3, 15, 30, 0, tzinfo=timezone.utc)


def ts(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(wf, status, conclusion="", created_min_ago=0.0, rid=1):
    return {
        "databaseId": rid,
        "workflowName": wf,
        "status": status,
        "conclusion": conclusion,
        "createdAt": ts(created_min_ago),
        "updatedAt": ts(created_min_ago),
        "headBranch": "main",
        "event": "push",
    }


class QueueDepthTests(unittest.TestCase):
    def test_queued_vs_running_split_per_workflow(self) -> None:
        runs = [
            run("portable", "queued", created_min_ago=40, rid=1),
            run("portable", "queued", created_min_ago=10, rid=2),
            run("portable", "in_progress", rid=3),
            run("docs", "pending", created_min_ago=5, rid=4),
            run("docs", "completed", "success", rid=5),
        ]
        q = qh.analyze_queue(runs, now=NOW)
        self.assertEqual((q["portable"].queued, q["portable"].running), (2, 1))
        self.assertEqual((q["docs"].queued, q["docs"].running), (1, 0))
        # Queue age is now-createdAt; oldest portable run waited ~40 min.
        self.assertAlmostEqual(q["portable"].max_age, 40 * 60, delta=1)
        self.assertAlmostEqual(q["portable"].median_age, 10 * 60, delta=60 * 30 + 1)


class LastGreenTests(unittest.TestCase):
    def test_runs_back_counts_from_newest(self) -> None:
        # newest-first: red, red, green, red  -> green is 2 runs back.
        runs = [
            run("ci", "completed", "failure", rid=10),
            run("ci", "completed", "failure", rid=11),
            run("ci", "completed", "success", created_min_ago=120, rid=12),
            run("ci", "completed", "failure", rid=13),
        ]
        g = qh.analyze_last_green(runs)["ci"]
        self.assertEqual(g.runs_back, 2)
        self.assertEqual(g.green_id, 12)
        self.assertEqual(g.total_in_window, 4)

    def test_no_green_in_window(self) -> None:
        runs = [run("ci", "completed", "failure", rid=i) for i in range(5)]
        g = qh.analyze_last_green(runs)["ci"]
        self.assertIsNone(g.runs_back)
        self.assertIsNone(g.green_id)


class RunnerAndConstraintTests(unittest.TestCase):
    def _runner(self, name, busy, labels, status="online"):
        return {"name": name, "status": status, "busy": busy,
                "labels": [{"name": l} for l in labels]}

    def test_pmu_serial_binding_constraint(self) -> None:
        api = {"total_count": 3, "runners": [
            self._runner("r1", True, ["pmu", "pmu-serial", "gate"]),
            self._runner("r2", True, ["pmu", "gate"]),
            self._runner("gate-only", False, ["gate"]),
        ]}
        rh = qh.analyze_runners(api)
        self.assertEqual((rh.pmu_total, rh.pmu_idle), (2, 0))
        self.assertEqual(rh.serial_runners, ["r1"])
        self.assertTrue(rh.serial_busy)
        queues = qh.analyze_queue([run("ci", "queued", rid=1)], now=NOW)
        bc = qh.binding_constraint(queues, rh)
        self.assertIsNotNone(bc)
        self.assertIn("PMU lane saturated", bc)
        self.assertIn("pmu-serial=r1", bc)

    def test_no_constraint_when_idle_pmu(self) -> None:
        api = {"total_count": 2, "runners": [
            self._runner("r1", False, ["pmu", "pmu-serial"]),
            self._runner("r2", True, ["pmu"]),
        ]}
        rh = qh.analyze_runners(api)
        queues = qh.analyze_queue([run("ci", "queued", rid=1)], now=NOW)
        self.assertIsNone(qh.binding_constraint(queues, rh))


class WaitDistributionTests(unittest.TestCase):
    def test_wait_and_duration_kept_separate(self) -> None:
        jobs = [
            {"run_id": 1, "wait": 600.0, "duration": 30.0},
            {"run_id": 2, "wait": 1200.0, "duration": 45.0},
            {"run_id": 3, "wait": 60.0, "duration": 900.0},
        ]
        run_wf = {1: "ci", 2: "ci", 3: "ci"}
        w = qh.analyze_waits(jobs, run_wf)["ci"]
        self.assertEqual(w.n, 3)
        self.assertEqual(w.wait_max, 1200.0)
        self.assertEqual(w.wait_median, 600.0)
        # Duration distribution is independent of wait.
        self.assertEqual(w.dur_median, 45.0)


def job(runner, started_min_ago, dur_min, rid=1):
    """A jobs-API-shaped record with absolute datetimes, as fetch_job_timings
    now returns. dur_min=None => still running (completed is None)."""
    started = NOW - timedelta(minutes=started_min_ago)
    completed = None if dur_min is None else started + timedelta(minutes=dur_min)
    return {"run_id": rid, "runner": runner, "started": started,
            "completed": completed, "wait": 0.0,
            "duration": None if dur_min is None else dur_min * 60.0}


class ConfiguredVsLiveTests(unittest.TestCase):
    def _api(self, statuses):
        return {"total_count": len(statuses), "runners": [
            {"name": f"r{i}", "status": s, "busy": False, "labels": []}
            for i, s in enumerate(statuses)]}

    def test_offline_is_configured_minus_live(self) -> None:
        # 3 configured, 1 offline => the silently-dead-runner finding.
        rh = qh.analyze_runners(self._api(["online", "online", "offline"]))
        self.assertEqual(rh.total, 3)
        self.assertEqual(rh.online, 2)
        self.assertEqual(rh.offline, 1)

    def test_no_offline_when_all_online(self) -> None:
        rh = qh.analyze_runners(self._api(["online", "online"]))
        self.assertEqual(rh.offline, 0)


class UtilizationTests(unittest.TestCase):
    def test_busy_over_capacity_and_selfhosted_filter(self) -> None:
        selfhosted = {"sh1", "sh2"}
        # Over the last 1h, two self-hosted jobs run 30m each (=1h busy), plus a
        # GitHub-hosted job that must NOT count toward self-hosted capacity.
        jobs = [
            job("sh1", started_min_ago=40, dur_min=30, rid=1),
            job("sh2", started_min_ago=20, dur_min=20, rid=2),
            job("GitHub Actions 3", started_min_ago=50, dur_min=45, rid=3),
        ]
        win_start = NOW - timedelta(hours=1)
        u = qh.analyze_utilization(jobs, selfhosted, n_runners=2,
                                   window_start=win_start, now=NOW,
                                   lower_bound=False, basis="test")
        # busy = 30m + 20m = 50m; capacity = 2 runners * 60m = 120m.
        self.assertAlmostEqual(u.busy_secs, 50 * 60, delta=1)
        self.assertAlmostEqual(u.capacity_secs, 120 * 60, delta=1)
        self.assertAlmostEqual(u.util_pct, 100.0 * 50 / 120, delta=0.1)
        self.assertEqual(u.n_jobs, 2)  # hosted job excluded

    def test_running_job_counts_up_to_now_and_clips_to_window(self) -> None:
        # A job that started 90m ago and is still running: only the last 60m fall
        # in a 1h window, so it contributes 60m of busy time, not 90m.
        jobs = [job("sh1", started_min_ago=90, dur_min=None, rid=1)]
        win_start = NOW - timedelta(hours=1)
        u = qh.analyze_utilization(jobs, {"sh1"}, n_runners=1,
                                   window_start=win_start, now=NOW,
                                   lower_bound=True, basis="test")
        self.assertAlmostEqual(u.busy_secs, 60 * 60, delta=1)
        self.assertTrue(u.lower_bound)


class PeakConcurrencyTests(unittest.TestCase):
    def test_overlap_counts_touching_does_not(self) -> None:
        selfhosted = {"a", "b", "c"}
        # a: [-50,-30), b: [-40,-20) overlap a on [-40,-30) => peak 2.
        # c: [-20,-10) touches b's end at -20 => NOT concurrent with b.
        jobs = [
            job("a", started_min_ago=50, dur_min=20, rid=1),
            job("b", started_min_ago=40, dur_min=20, rid=2),
            job("c", started_min_ago=20, dur_min=10, rid=3),
        ]
        win_start = NOW - timedelta(hours=2)
        p = qh.analyze_peak_concurrency(jobs, selfhosted, n_runners=3,
                                        window_start=win_start, now=NOW,
                                        lower_bound=False, basis="test")
        self.assertEqual(p.peak, 2)
        self.assertEqual(p.n_intervals, 3)


class RunWindowTests(unittest.TestCase):
    def test_merge_gate_separated_and_window_filter(self) -> None:
        runs = [
            run("CI", "completed", "success", created_min_ago=10, rid=1),
            run("CI", "completed", "failure", created_min_ago=20, rid=2),
            run("CI", "completed", "cancelled", created_min_ago=30, rid=3),
            run("Merge Gate", "completed", "failure", created_min_ago=15, rid=4),
            {**run("x", "completed", "success", rid=5), "event": "merge_group"},
            # Older than the 1h window => excluded from counts.
            run("CI", "completed", "failure", created_min_ago=120, rid=6),
        ]
        rw = qh.analyze_run_window(runs, NOW, window_hours=1.0)
        self.assertEqual(rw.started, 3)       # 3 non-gate CI runs in window
        self.assertEqual(rw.success, 1)
        self.assertEqual(rw.failure, 1)       # gate failure NOT counted here
        self.assertEqual(rw.cancelled, 1)
        self.assertEqual(rw.gate_started, 2)  # Merge Gate + merge_group event
        self.assertEqual(rw.gate_failure, 1)
        # Oldest run (120m) predates the 1h window => coverage is satisfied.
        self.assertTrue(rw.covers_window)

    def test_coverage_warning_when_window_not_spanned(self) -> None:
        runs = [run("CI", "completed", "success", created_min_ago=10, rid=1)]
        rw = qh.analyze_run_window(runs, NOW, window_hours=24.0)
        self.assertFalse(rw.covers_window)  # only 10m of history for a 24h window


class MergeGateClassifyTests(unittest.TestCase):
    def test_markers(self) -> None:
        self.assertTrue(qh._is_merge_gate("Merge Gate", "push"))
        self.assertTrue(qh._is_merge_gate("anything", "merge_group"))
        self.assertFalse(qh._is_merge_gate("Rust", "push"))
        self.assertFalse(qh._is_merge_gate("CI (portable)", "push"))


class GateTests(unittest.TestCase):
    def _patch(self, runs, api):
        self.addCleanup(setattr, qh, "fetch_runs", qh.fetch_runs)
        self.addCleanup(setattr, qh, "fetch_runners", qh.fetch_runners)
        qh.fetch_runs = lambda *a, **k: runs
        qh.fetch_runners = lambda *a, **k: api

    def test_deep_queue_trips_gate(self) -> None:
        runs = [run("portable", "queued", created_min_ago=5, rid=i)
                for i in range(qh.QUEUE_DEPTH_WARN + 2)]
        self._patch(runs, None)
        code, fields = qh.compute_gate("r", "gh", 100, now=NOW)
        self.assertEqual(code, 1)
        self.assertEqual(fields["state"], "red")
        self.assertGreaterEqual(fields["max_queue_depth"], qh.QUEUE_DEPTH_WARN)

    def test_stale_green_trips_gate(self) -> None:
        # Enough runs to clear GREEN_GATE_MIN_RUNS, none green.
        runs = [run("ci", "completed", "failure", rid=i)
                for i in range(qh.GREEN_GATE_MIN_RUNS + 1)]
        self._patch(runs, None)
        code, fields = qh.compute_gate("r", "gh", 100, now=NOW)
        self.assertEqual(code, 1)
        self.assertIn("ci", fields["stale_green"])

    def test_healthy_is_green(self) -> None:
        runs = [run("ci", "completed", "success", created_min_ago=1, rid=1),
                run("ci", "completed", "success", created_min_ago=2, rid=2)]
        self._patch(runs, None)
        code, fields = qh.compute_gate("r", "gh", 100, now=NOW)
        self.assertEqual(code, 0)
        self.assertEqual(fields["state"], "ok")


if __name__ == "__main__":
    unittest.main()

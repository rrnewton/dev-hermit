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

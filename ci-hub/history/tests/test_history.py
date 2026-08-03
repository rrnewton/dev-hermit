from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

HISTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HISTORY))

import ingest
import query


class IngestUnitTest(unittest.TestCase):
    def test_run_to_row_splits_queue_and_run(self):
        run = {
            "id": 42, "run_attempt": 1, "workflow_id": 7, "name": "CI",
            "event": "push", "head_branch": "main", "head_sha": "a" * 40,
            "status": "completed", "conclusion": "success",
            "created_at": "2026-08-03T00:00:00Z",
            "run_started_at": "2026-08-03T00:05:00Z",   # 300s queued
            "updated_at": "2026-08-03T00:12:00Z",       # 420s running
            "pull_requests": [{"number": 1151}],
        }
        row = ingest.run_to_row("rrnewton/hermit", run)
        self.assertEqual(row["queue_s"], "300")
        self.assertEqual(row["run_s"], "420")
        self.assertEqual(row["pull_requests"], "1151")
        self.assertEqual(row["head_sha"], "a" * 40)

    def test_run_s_blank_until_terminal(self):
        run = {"id": 1, "run_attempt": 1, "status": "in_progress",
               "created_at": "2026-08-03T00:00:00Z",
               "run_started_at": "2026-08-03T00:05:00Z",
               "updated_at": "2026-08-03T00:09:00Z"}
        row = ingest.run_to_row("r/x", run)
        self.assertEqual(row["queue_s"], "300")
        self.assertEqual(row["run_s"], "")   # not completed -> no run duration

    def test_upsert_idempotent_and_newest_wins(self):
        rows: dict = {}
        base = {"repo": "r/x", "run_id": "1", "run_attempt": "1"}
        early = {**base, "status": "in_progress", "conclusion": "",
                 "updated_at": "2026-08-03T00:09:00Z"}
        late = {**base, "status": "completed", "conclusion": "success",
                "updated_at": "2026-08-03T00:12:00Z"}
        ingest.upsert(rows, early)
        ingest.upsert(rows, early)          # duplicate observation
        self.assertEqual(len(rows), 1)      # no dup row
        ingest.upsert(rows, late)           # promotion
        self.assertEqual(rows[("r/x", "1", "1")]["conclusion"], "success")
        ingest.upsert(rows, early)          # stale re-observation must not regress
        self.assertEqual(rows[("r/x", "1", "1")]["status"], "completed")


class TempParentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self.tmp.name)
        (self.parent / "ignored" / "ci-hub").mkdir(parents=True)
        self._prev_env = os.environ.get("DEV_HERMIT_PARENT")
        os.environ["DEV_HERMIT_PARENT"] = str(self.parent)

    def tearDown(self):
        if self._prev_env is None:
            os.environ.pop("DEV_HERMIT_PARENT", None)
        else:
            os.environ["DEV_HERMIT_PARENT"] = self._prev_env
        self.tmp.cleanup()

    def _write_gha(self, rows):
        path = self.parent / "ignored" / "ci-hub" / "gha-runs.csv"
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=ingest.GHA_COLUMNS,
                               extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in ingest.GHA_COLUMNS})

    def _write_step_profiles(self, node_rows):
        prof = (self.parent / "checkout" / ".safe-ci-dag-runner" / "profiles")
        prof.mkdir(parents=True)
        path = prof / "step_profiles_machine_class.csv"
        cols = ["timestamp", "git_sha", "step", "elapsed_s", "user_s", "sys_s"]
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in node_rows:
                w.writerow(r)

    def test_node_cpu_budgets_thin_and_suggested(self):
        # build.x has 6 samples -> not thin, suggested = round(max_cpu * 1.5);
        # build.y has 2 samples -> thin, suggested None.
        rows = []
        cpus = [(10, 2), (12, 3), (11, 2), (13, 4), (20, 5), (9, 1)]  # max cpu=25
        for i, (u, s) in enumerate(cpus):
            rows.append({"timestamp": f"2026-08-03T0{i}:00:00Z", "git_sha": "a" * 40,
                         "step": "build.x", "elapsed_s": 30 + i,
                         "user_s": u, "sys_s": s})
        rows.append({"timestamp": "2026-08-03T00:00:00Z", "git_sha": "b" * 40,
                     "step": "build.y", "elapsed_s": 5, "user_s": 1, "sys_s": 1})
        rows.append({"timestamp": "2026-08-03T01:00:00Z", "git_sha": "b" * 40,
                     "step": "build.y", "elapsed_s": 6, "user_s": 2, "sys_s": 1})
        self._write_step_profiles(rows)

        out = {r["node"]: r for r in
               query.node_cpu_budgets(str(self.parent), None, None, 5)}
        self.assertEqual(out["build.x"]["n_samples"], 6)
        self.assertFalse(out["build.x"]["thin"])
        self.assertEqual(out["build.x"]["max_cpu_s"], 25.0)      # 20+5
        self.assertEqual(out["build.x"]["suggested_cpu_timeout"], 38)  # round(25*1.5)
        self.assertTrue(out["build.y"]["thin"])
        self.assertIsNone(out["build.y"]["suggested_cpu_timeout"])

    def test_node_cpu_budgets_skips_rows_missing_cpu(self):
        rows = [{"timestamp": "2026-08-03T00:00:00Z", "git_sha": "a" * 40,
                 "step": "test.a", "elapsed_s": 1, "user_s": "", "sys_s": ""}]
        self._write_step_profiles(rows)
        out = {r["node"]: r for r in
               query.node_cpu_budgets(str(self.parent), None, None, 5)}
        self.assertEqual(out["test.a"]["n_samples"], 0)   # no cpu sample
        self.assertEqual(out["test.a"]["max_wall_s"], 1.0)
        self.assertTrue(out["test.a"]["thin"])

    def test_green_time_interval_fraction(self):
        # Authoritative workflow "W". failure 00:00->01:00 (1h red), then
        # success 01:00->03:00 (evaluated to 'now'); we fix now via the last
        # interval only by adding a trailing success far in the past so the
        # open-ended tail is negligible. Instead assert the closed intervals.
        wf = "W"
        rows = [
            {"repo": "r/x", "run_id": "1", "run_attempt": "1", "workflow_name": wf,
             "head_branch": "main", "status": "completed", "conclusion": "failure",
             "created_at": "2026-08-03T00:00:00Z", "updated_at": "2026-08-03T00:00:00Z"},
            {"repo": "r/x", "run_id": "2", "run_attempt": "1", "workflow_name": wf,
             "head_branch": "main", "status": "completed", "conclusion": "success",
             "created_at": "2026-08-03T01:00:00Z", "updated_at": "2026-08-03T01:00:00Z"},
        ]
        self._write_gha(rows)
        res = query.green_time(str(self.parent), "r/x", None, [wf])
        self.assertEqual(res["samples"], 2)
        # First interval (failure) is exactly 1h; the success tail runs to now.
        # green_hours grows with wall time, so assert the red hour is fixed at 1.
        self.assertAlmostEqual(res["total_hours"] - res["green_hours"], 1.0, places=1)
        self.assertEqual(res["current_state"], "success")

    def test_green_time_ignores_non_main_and_non_authoritative(self):
        rows = [
            {"repo": "r/x", "run_id": "1", "run_attempt": "1", "workflow_name": "W",
             "head_branch": "feature", "status": "completed", "conclusion": "success",
             "created_at": "2026-08-03T00:00:00Z", "updated_at": "2026-08-03T00:00:00Z"},
            {"repo": "r/x", "run_id": "2", "run_attempt": "1", "workflow_name": "Other",
             "head_branch": "main", "status": "completed", "conclusion": "success",
             "created_at": "2026-08-03T00:00:00Z", "updated_at": "2026-08-03T00:00:00Z"},
        ]
        self._write_gha(rows)
        res = query.green_time(str(self.parent), "r/x", None, ["W"])
        self.assertEqual(res["samples"], 0)
        self.assertIsNone(res["green_pct"])


class HelperTest(unittest.TestCase):
    def test_percentile_nearest_rank(self):
        self.assertEqual(query.percentile([1, 2, 3, 4], 50), 2)
        self.assertEqual(query.percentile([1, 2, 3, 4], 95), 4)
        self.assertIsNone(query.percentile([], 50))


if __name__ == "__main__":
    unittest.main()

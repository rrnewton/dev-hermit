from __future__ import annotations

import csv
import json
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

    def _gha_wf(self, sha, concl, created, updated, wf="W", status="completed",
                run_id=None):
        return {"repo": "r/x", "run_id": run_id or (sha + concl),
                "run_attempt": "1", "workflow_name": wf, "head_branch": "main",
                "head_sha": sha, "status": status, "conclusion": concl,
                "created_at": created, "updated_at": updated}

    def test_green_time_red_reign_is_fixed_hour(self):
        # Commit A: run created+terminal-failure at 00:00; commit B: created
        # 01:00 (bounds A's reign to exactly 1h), success. A completes instantly
        # so its whole 1h reign is red; B's success tail runs to now.
        self._write_gha([
            self._gha_wf("a" * 40, "failure", "2026-08-03T00:00:00Z",
                         "2026-08-03T00:00:00Z"),
            self._gha_wf("b" * 40, "success", "2026-08-03T01:00:00Z",
                         "2026-08-03T01:00:00Z"),
        ])
        res = query.green_time(str(self.parent), "r/x", None, ["W"])
        self.assertEqual(res["samples"], 2)
        self.assertAlmostEqual(res["red_hours"], 1.0, places=1)
        # denominator is fully accounted: the four states sum to total.
        self.assertAlmostEqual(
            res["green_hours"] + res["red_hours"] + res["no_result_hours"]
            + res["gap_hours"], res["total_hours"], places=1)
        self.assertEqual(res["current_state"], "green")
        self.assertEqual(res["definition_date"], query.GREEN_TIME_DEFINITION_DATE)

    def test_green_time_pending_tip_is_gap_not_green(self):
        # The owner's example: main has zero reds but the current tip's
        # authoritative run is still pending -> the tip reign is GAP, never green.
        self._write_gha([
            self._gha_wf("a" * 40, "success", "2026-08-03T00:00:00Z",
                         "2026-08-03T00:00:00Z"),
            self._gha_wf("b" * 40, "", "2026-08-03T01:00:00Z",
                         "2026-08-03T01:00:00Z", status="in_progress"),
        ])
        res = query.green_time(str(self.parent), "r/x", None, ["W"])
        self.assertEqual(res["current_state"], "gap")
        self.assertEqual(res["current_reason"], "pending")
        self.assertGreater(res["gap_hours"], 0.0)
        self.assertEqual(res["red_hours"], 0.0)  # zero reds, yet not fully green

    def test_green_time_cancelled_is_no_result_not_red(self):
        # A supersede/manual cancel is a destroyed answer, not a failure: it is
        # no_result, never red, and never green.
        self._write_gha([
            self._gha_wf("a" * 40, "cancelled", "2026-08-03T00:00:00Z",
                         "2026-08-03T00:00:00Z"),
            self._gha_wf("b" * 40, "success", "2026-08-03T01:00:00Z",
                         "2026-08-03T01:00:00Z"),
        ])
        res = query.green_time(str(self.parent), "r/x", None, ["W"])
        self.assertAlmostEqual(res["no_result_hours"], 1.0, places=1)
        self.assertEqual(res["red_hours"], 0.0)

    def _write_jobs(self, rows):
        path = self.parent / "ignored" / "ci-hub" / "gha-jobs.csv"
        cols = ["repo", "run_id", "job_id", "name", "conclusion",
                "started_at", "completed_at"]
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})

    def test_green_time_case7_job_failed_before_cancel_is_red(self):
        # Seventh case: run-level cancelled, but a job FAILED at 00:30, before the
        # run's cancel at 00:40 -> ORDERING says the failure was independent ->
        # the reign is RED, not no_result.
        self._write_gha([
            self._gha_wf("a" * 40, "cancelled", "2026-08-03T00:00:00Z",
                         "2026-08-03T00:40:00Z", run_id="R1"),
            self._gha_wf("b" * 40, "success", "2026-08-03T01:00:00Z",
                         "2026-08-03T01:00:00Z", run_id="R2"),
        ])
        self._write_jobs([
            {"repo": "r/x", "run_id": "R1", "job_id": "j1", "name": "build",
             "conclusion": "failure", "completed_at": "2026-08-03T00:30:00Z"},
        ])
        res = query.green_time(str(self.parent), "r/x", None, ["W"])
        # reign 00:00-01:00: pending until the run resolves at 00:40 (0.67h gap),
        # then the verdict 00:40-01:00 (0.33h) is RED via the ordering promotion.
        self.assertAlmostEqual(res["red_hours"], 0.33, places=1)
        self.assertAlmostEqual(res["gap_hours"], 0.67, places=1)
        self.assertEqual(res["no_result_hours"], 0.0)
        self.assertEqual(res["job_level_red_promotions"], 1)

    def test_green_time_case7_job_killed_by_cancel_stays_no_result(self):
        # A job killed BY the cancel (its red conclusion completes AFTER the
        # cancel moment) is not an independent failure -> stays no_result.
        self._write_gha([
            self._gha_wf("a" * 40, "cancelled", "2026-08-03T00:00:00Z",
                         "2026-08-03T00:40:00Z", run_id="R1"),
            self._gha_wf("b" * 40, "success", "2026-08-03T01:00:00Z",
                         "2026-08-03T01:00:00Z", run_id="R2"),
        ])
        self._write_jobs([
            {"repo": "r/x", "run_id": "R1", "job_id": "j1", "name": "build",
             "conclusion": "failure", "completed_at": "2026-08-03T00:55:00Z"},
        ])
        res = query.green_time(str(self.parent), "r/x", None, ["W"])
        # completed AFTER the 00:40 cancel -> killed by it -> stays no_result.
        self.assertAlmostEqual(res["no_result_hours"], 0.33, places=1)
        self.assertEqual(res["red_hours"], 0.0)
        self.assertEqual(res["job_level_red_promotions"], 0)

    def test_green_time_case7_inert_without_job_store(self):
        # No gha-jobs.csv -> the discriminator is inert and cancelled stays
        # no_result (conservative), identical to the run-level-only behavior.
        self._write_gha([
            self._gha_wf("a" * 40, "cancelled", "2026-08-03T00:00:00Z",
                         "2026-08-03T00:40:00Z", run_id="R1"),
            self._gha_wf("b" * 40, "success", "2026-08-03T01:00:00Z",
                         "2026-08-03T01:00:00Z", run_id="R2"),
        ])
        res = query.green_time(str(self.parent), "r/x", None, ["W"])
        self.assertAlmostEqual(res["no_result_hours"], 0.33, places=1)
        self.assertEqual(res["red_hours"], 0.0)
        self.assertEqual(res["job_level_red_promotions"], 0)

    def test_green_time_commit_without_authoritative_run_is_gap_no_record(self):
        # Commit B ran only a NON-authoritative workflow: no authoritative check
        # record -> gap(no-record), never carried-forward as A's green.
        self._write_gha([
            self._gha_wf("a" * 40, "success", "2026-08-03T00:00:00Z",
                         "2026-08-03T00:00:00Z"),
            self._gha_wf("b" * 40, "success", "2026-08-03T01:00:00Z",
                         "2026-08-03T01:00:00Z", wf="Other"),
        ])
        res = query.green_time(str(self.parent), "r/x", None, ["W"])
        self.assertEqual(res["current_state"], "gap")
        self.assertEqual(res["current_reason"], "no-record")
        self.assertAlmostEqual(res["green_hours"], 1.0, places=1)  # only A's reign

    def test_green_time_ignores_non_main(self):
        self._write_gha([
            self._gha_wf("a" * 40, "success", "2026-08-03T00:00:00Z",
                         "2026-08-03T00:00:00Z"),
        ])
        # override branch to feature by rewriting the single row
        self._write_gha([{**self._gha_wf("a" * 40, "success",
                          "2026-08-03T00:00:00Z", "2026-08-03T00:00:00Z"),
                          "head_branch": "feature"}])
        res = query.green_time(str(self.parent), "r/x", None, ["W"])
        self.assertEqual(res["samples"], 0)
        self.assertIsNone(res["green_pct"])

    def test_green_time_multi_workflow_requires_all_success(self):
        # Two authoritative workflows at commit A: one success, one failure ->
        # combined red (green requires ALL).
        self._write_gha([
            self._gha_wf("a" * 40, "success", "2026-08-03T00:00:00Z",
                         "2026-08-03T00:00:00Z", wf="W1"),
            self._gha_wf("a" * 40, "failure", "2026-08-03T00:00:00Z",
                         "2026-08-03T00:00:00Z", wf="W2"),
            self._gha_wf("b" * 40, "success", "2026-08-03T01:00:00Z",
                         "2026-08-03T01:00:00Z", wf="W1"),
            self._gha_wf("b" * 40, "success", "2026-08-03T01:00:00Z",
                         "2026-08-03T01:00:00Z", wf="W2"),
        ])
        res = query.green_time(str(self.parent), "r/x", None, ["W1", "W2"])
        self.assertAlmostEqual(res["red_hours"], 1.0, places=1)  # A's reign red
        self.assertEqual(res["current_state"], "green")           # B all-success

    def test_green_time_trend_buckets_per_day(self):
        self._write_gha([
            self._gha_wf("a" * 40, "failure", "2026-08-01T00:00:00Z",
                         "2026-08-01T00:00:00Z"),
            self._gha_wf("b" * 40, "success", "2026-08-02T00:00:00Z",
                         "2026-08-02T00:00:00Z"),
        ])
        tr = query.green_time_trend(str(self.parent), "r/x", None, ["W"], "day")
        self.assertEqual(tr["bucket"], "day")
        self.assertGreaterEqual(len(tr["buckets"]), 1)
        # first day bucket is the failure reign -> 0% green
        self.assertEqual(tr["buckets"][0]["green_pct"], 0.0)

    def test_green_time_append_log_writes_jsonl(self):
        self._write_gha([
            self._gha_wf("a" * 40, "success", "2026-08-03T00:00:00Z",
                         "2026-08-03T00:00:00Z"),
        ])
        res = query.green_time(str(self.parent), "r/x", None, ["W"])
        path = str(self.parent / "gtlog.jsonl")
        query.append_green_time_log(str(self.parent), res, path)
        query.append_green_time_log(str(self.parent), res, path)
        with open(path) as fh:
            lines = [json.loads(x) for x in fh if x.strip()]
        self.assertEqual(len(lines), 2)  # appends, never truncates
        self.assertEqual(lines[0]["repo"], "r/x")
        self.assertIn("green_pct", lines[0])


    def _gha_run(self, **over):
        base = {
            "repo": "rrnewton/hermit", "run_id": "1", "run_attempt": "1",
            "workflow_name": "CI", "head_branch": "main", "pull_requests": "",
            "status": "completed", "conclusion": "success",
            "created_at": "2026-08-03T00:00:00Z", "queue_s": "0", "run_s": "10",
            "html_url": "https://x/1",
        }
        base.update(over)
        return base

    def test_recent_runs_newest_first_and_pr_ref(self):
        self._write_gha([
            self._gha_run(run_id="a", created_at="2026-08-03T01:00:00Z"),
            self._gha_run(run_id="b", created_at="2026-08-03T03:00:00Z",
                          pull_requests="1561"),
            self._gha_run(run_id="c", created_at="2026-08-03T02:00:00Z"),
        ])
        res = query.recent_runs(str(self.parent), None, None, None, None, 10)
        ids = [r["run_id"] for r in res["runs"]]
        self.assertEqual(ids, ["b", "c", "a"])           # newest first
        self.assertEqual(res["runs"][0]["ref"], "#1561")  # PR beats branch
        self.assertEqual(res["runs"][1]["ref"], "main")

    def test_recent_runs_since_status_and_limit(self):
        self._write_gha([
            self._gha_run(run_id="old", created_at="2026-08-01T00:00:00Z"),
            self._gha_run(run_id="q1", created_at="2026-08-03T01:00:00Z",
                          status="queued", conclusion=""),
            self._gha_run(run_id="q2", created_at="2026-08-03T02:00:00Z",
                          status="queued", conclusion=""),
        ])
        # since drops the 08-01 row; status keeps only queued; limit caps output.
        res = query.recent_runs(str(self.parent), None, "2026-08-02",
                                None, "queued", 1)
        self.assertEqual(res["total_matched"], 2)   # both queued survive filter
        self.assertEqual(res["shown"], 1)           # limit
        self.assertEqual(res["runs"][0]["run_id"], "q2")
        self.assertEqual(res["runs"][0]["conclusion"], "queued")  # falls back to status

    def test_recent_runs_queue_outlier_and_slowest(self):
        # A realistic shape: many instant-start runs (p95 stays 0, so the 300s
        # floor governs) plus one stuck-for-hours run that must be flagged.
        rows = [self._gha_run(run_id=f"fast{i}",
                              created_at=f"2026-08-03T03:{i:02d}:00Z",
                              queue_s="0") for i in range(30)]
        rows.append(self._gha_run(run_id="stuck",
                                  created_at="2026-08-03T01:00:00Z",
                                  queue_s="26558", run_s="137"))
        self._write_gha(rows)
        res = query.recent_runs(str(self.parent), None, None, None, None, 10,
                                slowest=True)
        self.assertEqual(res["runs"][0]["run_id"], "stuck")   # slowest first
        self.assertTrue(res["runs"][0]["queue_outlier"])
        self.assertFalse(res["runs"][1]["queue_outlier"])
        self.assertEqual(res["window_outliers"], 1)
        # Rendered table marks the outlier and never truncates the workflow name.
        out = query.render_recent(res, 10)
        self.assertIn("!", out)
        self.assertIn("26558", out)

    def test_queued_run_gets_offline_lower_bound_not_zero(self):
        # A still-queued run has run_started_at == created_at (GitHub placeholder)
        # => queue_s == 0. The listing must replace that misleading 0 with the
        # snapshot-anchored lower bound, keep queue_s itself untouched, and flag
        # the run as an outlier off the lower bound (not the stored 0).
        created = "2026-08-03T00:00:00Z"
        self._write_gha([
            self._gha_run(run_id="term", created_at="2026-08-03T05:00:00Z",
                          conclusion="success", queue_s="0"),
            self._gha_run(run_id="stuck", created_at=created,
                          run_started_at=created,  # placeholder == created
                          status="queued", conclusion="", queue_s="0"),
        ])
        # Pin the snapshot clock: set the store mtime to created + 2h.
        store = self.parent / "ignored" / "ci-hub" / "gha-runs.csv"
        snap = query._epoch(created) + 7200
        os.utime(store, (snap, snap))

        res = query.recent_runs(str(self.parent), None, None, None, None, 10,
                                slowest=True)
        stuck = next(r for r in res["runs"] if r["run_id"] == "stuck")
        term = next(r for r in res["runs"] if r["run_id"] == "term")
        self.assertEqual(stuck["queue_s"], 0.0)              # stored value untouched
        self.assertEqual(stuck["queue_lower_bound_s"], 7200)  # snapshot - created
        self.assertTrue(stuck["queue_outlier"])              # flagged off the LB
        self.assertIsNone(term["queue_lower_bound_s"])       # terminal: no LB
        self.assertEqual(res["runs"][0]["run_id"], "stuck")  # slowest = LB wins
        # queue_s percentiles must stay measured-only (the LB never leaks in).
        self.assertEqual(res["queue_p95_s"], 0.0)
        out = query.render_recent(res, 10)
        self.assertIn(">=7200", out)
        self.assertIn("still queued as of snapshot", out)


class HelperTest(unittest.TestCase):
    def test_percentile_nearest_rank(self):
        self.assertEqual(query.percentile([1, 2, 3, 4], 50), 2)
        self.assertEqual(query.percentile([1, 2, 3, 4], 95), 4)
        self.assertIsNone(query.percentile([], 50))


if __name__ == "__main__":
    unittest.main()

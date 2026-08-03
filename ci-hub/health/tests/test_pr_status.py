#!/usr/bin/env python3
"""Tests for the dev-hermit adapter around agent-utils/pr-landing-planner."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pr_status


class PlannerAdapterTests(unittest.TestCase):
    def test_command_uses_pinned_agent_utils_front_door(self) -> None:
        command = pr_status.planner_command("rrnewton/hermit", 7)
        self.assertEqual(command[1:3], ["pr-landing-planner", "status"])
        self.assertIn("--net-wrapper", command)
        self.assertIn("with-proxy", command)
        self.assertIn("--format", command)
        self.assertIn("json", command)

    @mock.patch("pr_status.subprocess.run")
    def test_fetch_uses_planner_schema_without_reimplementing_ci(self, run: mock.Mock) -> None:
        payload = {
            "summary": {
                "open": 3,
                "green": 1,
                "red": 1,
                "pending": 1,
                "real_reds": 1,
                "outage_suspected": False,
            },
            "prs": [{"pr": 12, "ci": "red", "red_class": "real", "title": "fix"}],
        }
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr=""
        )
        status = pr_status.fetch_repo_status("rrnewton/hermit")
        self.assertEqual(status.open, 3)
        self.assertTrue(status.unhealthy)
        self.assertEqual(status.prs[0]["red_class"], "real")

    @mock.patch("pr_status.time.sleep")
    @mock.patch("pr_status.subprocess.run")
    def test_fetch_retries_transient_graphql_504(
        self, run: mock.Mock, sleep: mock.Mock
    ) -> None:
        payload = {
            "summary": {
                "open": 0,
                "green": 0,
                "red": 0,
                "pending": 0,
                "real_reds": 0,
                "outage_suspected": False,
            },
            "prs": [],
        }
        run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="HTTP 504"),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(payload), stderr=""
            ),
        ]
        status = pr_status.fetch_repo_status("rrnewton/hermit")
        self.assertEqual(status.open, 0)
        sleep.assert_called_once_with(1)

    @mock.patch("pr_status.time.sleep")
    @mock.patch("pr_status.subprocess.run")
    def test_fetch_retries_identity_race(self, run: mock.Mock, sleep: mock.Mock) -> None:
        payload = {
            "summary": {
                "open": 0,
                "green": 0,
                "red": 0,
                "pending": 0,
                "real_reds": 0,
                "outage_suspected": False,
            },
            "prs": [],
        }
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="PR #1 changed during collection"
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(payload), stderr=""
            ),
        ]
        status = pr_status.fetch_repo_status("rrnewton/hermit")
        self.assertEqual(status.open, 0)
        sleep.assert_called_once_with(1)

    @mock.patch("pr_status.subprocess.run")
    def test_timeout_yields_unavailable_not_hang(self, run: mock.Mock) -> None:
        run.side_effect = subprocess.TimeoutExpired(cmd="planner", timeout=1.0)
        with self.assertRaises(pr_status.RepoUnavailable):
            pr_status.fetch_repo_status("rrnewton/hermit", timeout=1.0)
        run.assert_called_once()  # a timeout is terminal, not retried

    @mock.patch("pr_status.subprocess.run")
    def test_collect_records_partial_result_on_timeout(self, run: mock.Mock) -> None:
        run.side_effect = subprocess.TimeoutExpired(cmd="planner", timeout=1.0)
        statuses = pr_status.collect_statuses(
            ["rrnewton/hermit"],
            warn_threshold=10,
            per_repo_timeout=1.0,
            overall_deadline=5.0,
        )
        self.assertEqual(len(statuses), 1)
        self.assertFalse(statuses[0].available)
        self.assertFalse(statuses[0].unhealthy)
        self.assertIn("exceeded", statuses[0].reason)

    def test_collect_marks_unavailable_when_deadline_exhausted(self) -> None:
        statuses = pr_status.collect_statuses(
            ["rrnewton/hermit", "rrnewton/reverie"],
            warn_threshold=10,
            per_repo_timeout=300.0,
            overall_deadline=-1.0,  # already past the deadline
        )
        self.assertEqual(len(statuses), 2)
        self.assertTrue(all(not status.available for status in statuses))
        self.assertIn("deadline", statuses[0].reason)

    def test_render_degraded_reports_partial(self) -> None:
        available = pr_status.RepoStatus(
            repo="rrnewton/reverie",
            open=1,
            green=1,
            red=0,
            pending=0,
            real_reds=0,
            outage_suspected=False,
            prs=(),
        )
        unavailable = pr_status._unavailable("rrnewton/hermit", "planner exceeded 300s")
        report = pr_status.render_report([available, unavailable], warn_threshold=10)
        self.assertIn("DEGRADED", report)
        self.assertIn("UNAVAILABLE", report)
        self.assertIn("PARTIAL RESULT", report)

    def test_render_distinguishes_benign_red_from_unhealthy(self) -> None:
        status = pr_status.RepoStatus(
            repo="rrnewton/hermit",
            open=2,
            green=1,
            red=1,
            pending=0,
            real_reds=0,
            outage_suspected=False,
            prs=(),
        )
        report = pr_status.render_report([status], warn_threshold=10)
        self.assertIn("CI health: HEALTHY", report)
        self.assertIn("red=1", report)


if __name__ == "__main__":
    unittest.main()

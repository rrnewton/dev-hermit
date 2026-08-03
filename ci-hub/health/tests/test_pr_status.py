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

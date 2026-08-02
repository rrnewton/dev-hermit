#!/usr/bin/env python3
"""Tests for scripts/github_main_health.py."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from scripts import github_main_health


def run(*, workflow: str, conclusion: str, created_at: str = "2026-08-02T00:00:00Z") -> dict[str, str]:
    return {
        "workflowName": workflow,
        "headSha": "a" * 40,
        "status": "completed",
        "conclusion": conclusion,
        "url": f"https://example.test/{workflow}",
        "createdAt": created_at,
    }


class MainHealthTests(unittest.TestCase):
    def test_red_wins(self) -> None:
        runs = (
            github_main_health.MainRun("docs", "a" * 40, "completed", "success", "u1", "1"),
            github_main_health.MainRun("ci", "a" * 40, "completed", "failure", "u2", "1"),
        )
        self.assertEqual(github_main_health.classify_current_runs(runs), "red")

    def test_pending_is_not_green(self) -> None:
        runs = (
            github_main_health.MainRun("ci", "a" * 40, "in_progress", "", "u", "1"),
        )
        self.assertEqual(github_main_health.classify_current_runs(runs), "pending")

    @mock.patch("scripts.github_main_health.subprocess.run")
    def test_evaluate_uses_proxy_and_latest_attempt(self, run_command: mock.Mock) -> None:
        payload = [
            run(workflow="ci", conclusion="success", created_at="2026-08-02T00:01:00Z"),
            run(workflow="ci", conclusion="failure", created_at="2026-08-02T00:00:00Z"),
        ]
        run_command.side_effect = (
            subprocess.CompletedProcess(args=[], returncode=0, stdout="a" * 40 + "\n", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload), stderr=""),
        )
        health = github_main_health.evaluate_repo("rrnewton/hermit")
        self.assertEqual(health.state, "green")
        self.assertEqual(len(health.runs), 1)
        for call in run_command.call_args_list:
            self.assertEqual(call.args[0][:2], ("with-proxy", "gh"))
        list_command = run_command.call_args_list[1].args[0]
        self.assertIn("--branch", list_command)
        self.assertIn("--event", list_command)

    def test_render_red_hard_warning(self) -> None:
        health = (
            github_main_health.RepoMainHealth(
                repo="rrnewton/hermit",
                main_sha="a" * 40,
                state="red",
                runs=(
                    github_main_health.MainRun(
                        "ci", "a" * 40, "completed", "failure", "https://run", "1"
                    ),
                ),
            ),
        )
        report = github_main_health.render_report(health)
        self.assertIn("HARD WARNING: GITHUB MAIN IS RED", report)
        self.assertIn("RED", report)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Focused unit tests for scripts/health-poll.py."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("health-poll.py")
SPEC = importlib.util.spec_from_file_location("health_poll", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
health_poll = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = health_poll
SPEC.loader.exec_module(health_poll)


class CadenceTests(unittest.TestCase):
    def test_due_boundaries(self) -> None:
        self.assertTrue(health_poll.is_due("x", 0, 100, {"x": 100}))
        self.assertTrue(health_poll.is_due("x", 60, 100, {}))
        self.assertFalse(health_poll.is_due("x", 60, 159, {"x": 100}))
        self.assertTrue(health_poll.is_due("x", 60, 160, {"x": 100}))

    def test_state_loader_ignores_invalid_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state"
            path.write_text("# comment\nci-health=10\nbad\nx=nope\n", encoding="utf-8")
            self.assertEqual(health_poll.load_fired_state(path), {"ci-health": 10})


class CiTests(unittest.TestCase):
    def classify(self, sha: str, status: str, conclusion: str | None):
        runs = [
            {
                "name": health_poll.AUTHORITATIVE_WORKFLOW,
                "head_branch": "main",
                "event": "push",
                "head_sha": sha,
                "status": status,
                "conclusion": conclusion,
                "html_url": "https://example.invalid/run",
            }
        ]
        return health_poll.classify_current_ci(runs, sha)

    def test_green_current_main(self) -> None:
        status, _, actions = self.classify("a" * 40, "completed", "success")
        self.assertEqual(status, "OK")
        self.assertEqual(actions, ())

    def test_pending_current_main(self) -> None:
        status, _, actions = self.classify("b" * 40, "in_progress", None)
        self.assertEqual(status, "WARN")
        self.assertEqual(actions[0].kind, "monitor-main-ci")

    def test_red_current_main(self) -> None:
        status, _, actions = self.classify("c" * 40, "completed", "failure")
        self.assertEqual(status, "CRIT")
        self.assertEqual(actions[0].kind, "repair-main-ci")


class HistoryTests(unittest.TestCase):
    def test_sliding_window_counts_latest_authoritative_run(self) -> None:
        now = 2_000_000
        inside = health_poll.datetime.fromtimestamp(
            now - 60, tz=health_poll.timezone.utc
        ).isoformat()
        outside = health_poll.datetime.fromtimestamp(
            now - 100_000, tz=health_poll.timezone.utc
        ).isoformat()
        commits = [
            {"sha": "green", "commit": {"committer": {"date": inside}}},
            {"sha": "red", "commit": {"committer": {"date": inside}}},
            {"sha": "missing", "commit": {"committer": {"date": inside}}},
            {"sha": "old", "commit": {"committer": {"date": outside}}},
        ]
        runs = [
            {
                "name": health_poll.AUTHORITATIVE_WORKFLOW,
                "head_branch": "main",
                "event": "push",
                "head_sha": "green",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "name": health_poll.AUTHORITATIVE_WORKFLOW,
                "head_branch": "main",
                "event": "push",
                "head_sha": "red",
                "status": "completed",
                "conclusion": "failure",
            },
        ]
        counts = health_poll.summarize_main_window(commits, runs, now, 24)
        self.assertEqual(
            counts,
            {"commits": 3, "green": 1, "red": 1, "pending": 0, "missing": 1},
        )

    def test_latest_authoritative_run_wins_for_a_sha(self) -> None:
        now = 2_000_000
        inside = health_poll.datetime.fromtimestamp(
            now - 60, tz=health_poll.timezone.utc
        ).isoformat()
        commits = [{"sha": "same", "commit": {"committer": {"date": inside}}}]
        runs = [
            {
                "name": health_poll.AUTHORITATIVE_WORKFLOW,
                "head_branch": "main",
                "event": "push",
                "head_sha": "same",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "name": health_poll.AUTHORITATIVE_WORKFLOW,
                "head_branch": "main",
                "event": "push",
                "head_sha": "same",
                "status": "completed",
                "conclusion": "failure",
            },
        ]
        counts = health_poll.summarize_main_window(commits, runs, now, 24)
        self.assertEqual(counts["green"], 1)
        self.assertEqual(counts["red"], 0)


if __name__ == "__main__":
    unittest.main()

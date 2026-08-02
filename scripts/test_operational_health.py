#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from scripts import operational_health


class OperationalHealthTest(unittest.TestCase):
    def capture(self, function: object, *args: object, **kwargs: object) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = function(*args, **kwargs)  # type: ignore[operator]
        return int(result), output.getvalue()

    def test_github_main_red_is_a_warning(self) -> None:
        repos = [SimpleNamespace(repo="example/project", state="red")]
        with mock.patch.object(
            operational_health.github_main_health,
            "evaluate_repo",
            return_value=repos[0],
        ), mock.patch.object(
            operational_health.github_main_health,
            "overall_state",
            return_value="red",
        ):
            result, output = self.capture(operational_health.github_main_gate)
        self.assertEqual(result, 1)
        self.assertIn("state=red", output)
        self.assertIn("summary=example/project:red", output)

    def test_github_main_pending_is_not_a_hard_warning(self) -> None:
        repos = [SimpleNamespace(repo="example/project", state="pending")]
        with mock.patch.object(
            operational_health.github_main_health,
            "evaluate_repo",
            return_value=repos[0],
        ), mock.patch.object(
            operational_health.github_main_health,
            "overall_state",
            return_value="pending",
        ):
            result, output = self.capture(operational_health.github_main_gate)
        self.assertEqual(result, 0)
        self.assertIn("state=pending", output)

    def test_pull_request_red_count_is_a_warning(self) -> None:
        pulls = [
            SimpleNamespace(ci_status="red"),
            SimpleNamespace(ci_status="green"),
            SimpleNamespace(ci_status="pending"),
        ]
        with mock.patch.object(
            operational_health.pr_status,
            "fetch_open_prs",
            side_effect=[pulls, []],
        ):
            result, output = self.capture(operational_health.pull_request_gate)
        self.assertEqual(result, 1)
        self.assertIn("total=3", output)
        self.assertIn("red=1", output)
        self.assertIn("pending=1", output)

    def test_primary_snapshot_failure_is_a_structured_warning(self) -> None:
        def blocked(*_args: object, **kwargs: object) -> int:
            kwargs["err"].write("dirty Hermit primary\n")  # type: ignore[attr-defined]
            return 1

        with mock.patch.object(
            operational_health.primary_checkout,
            "checkout_fresh",
            side_effect=blocked,
        ):
            result, output = self.capture(operational_health.primary_snapshot_gate)
        self.assertEqual(result, 1)
        self.assertIn("state=blocked", output)
        self.assertIn("summary=dirty Hermit primary", output)

    def test_broken_and_silent_active_agents_are_stuck(self) -> None:
        agents = [
            {"name": "broken", "status": "crashed", "last_activity": 9_999},
            {"name": "silent", "status": "working", "last_activity": 1_000},
            {"name": "fresh", "status": "working", "last_activity": 9_900},
            {"name": "idle", "status": "idle", "last_activity": 1_000},
        ]
        stuck = operational_health.classify_stuck_agents(
            agents,
            now=10_000,
            stuck_after_secs=3_600,
        )
        self.assertEqual(
            stuck,
            [("broken", "crashed"), ("silent", "working-silent-150m")],
        )

    def test_millisecond_activity_timestamp_is_normalized(self) -> None:
        agents = [
            {
                "name": "fresh",
                "status": "running",
                "last_activity": 1_784_999_900_000,
            }
        ]
        self.assertEqual(
            operational_health.classify_stuck_agents(
                agents,
                now=1_785_000_000,
                stuck_after_secs=3_600,
            ),
            [],
        )

    def test_agent_gate_requires_a_snapshot(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            result, output = self.capture(operational_health.agent_gate)
        self.assertEqual(result, 1)
        self.assertIn("state=unknown", output)
        self.assertIn("ORC-agent-snapshot-missing", output)

    def test_agent_gate_emits_stuck_names(self) -> None:
        snapshot = '[{"name":"worker","status":"failed"}]'
        result, output = self.capture(
            operational_health.agent_gate,
            snapshot,
            now=10_000,
        )
        self.assertEqual(result, 1)
        self.assertIn("state=stuck", output)
        self.assertIn("names=worker", output)


if __name__ == "__main__":
    unittest.main()

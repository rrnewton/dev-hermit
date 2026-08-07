#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import operational_health


def pr_status_unavailable(reason: str) -> Exception:
    """A repo-query failure as pr_status raises it (a RuntimeError subclass)."""
    return operational_health.pr_status.RepoUnavailable(reason)


class OperationalHealthTest(unittest.TestCase):
    def capture(
        self, function: object, *args: object, **kwargs: object
    ) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = function(*args, **kwargs)  # type: ignore[operator]
        return int(result), output.getvalue()

    def test_github_main_red_is_a_warning(self) -> None:
        repos = [SimpleNamespace(repo="example/project", state="red", available=True)]
        with mock.patch.object(
            operational_health.github_main_health,
            "collect_health",
            return_value=repos,
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
        repos = [
            SimpleNamespace(repo="example/project", state="pending", available=True)
        ]
        with mock.patch.object(
            operational_health.github_main_health,
            "collect_health",
            return_value=repos,
        ), mock.patch.object(
            operational_health.github_main_health,
            "overall_state",
            return_value="pending",
        ):
            result, output = self.capture(operational_health.github_main_gate)
        self.assertEqual(result, 0)
        self.assertIn("state=pending", output)

    def test_pull_request_red_count_is_a_warning(self) -> None:
        statuses = [
            SimpleNamespace(
                open=3,
                red=1,
                green=1,
                pending=1,
                real_reds=1,
                outage_suspected=False,
            ),
            SimpleNamespace(
                open=0,
                red=0,
                green=0,
                pending=0,
                real_reds=0,
                outage_suspected=False,
            ),
        ]
        with mock.patch.object(
            operational_health.pr_status,
            "fetch_repo_status",
            side_effect=statuses,
        ):
            result, output = self.capture(operational_health.pull_request_gate)
        self.assertEqual(result, 1)
        self.assertIn("total=3", output)
        self.assertIn("red=1", output)
        self.assertIn("pending=1", output)

    def test_pull_request_one_repo_unavailable_is_degraded_not_lost(self) -> None:
        # A slow/blocked repo must not discard the other repo's real data: the
        # answer is DEGRADED (partial), distinct from "PRs are red".
        healthy = SimpleNamespace(
            open=2, red=0, green=2, pending=0, real_reds=0, outage_suspected=False
        )
        with mock.patch.object(
            operational_health.pr_status, "DEFAULT_REPOS", ["a/one", "b/two"]
        ), mock.patch.object(
            operational_health.pr_status,
            "fetch_repo_status",
            side_effect=[healthy, pr_status_unavailable("b/two blocked")],
        ):
            result, output = self.capture(operational_health.pull_request_gate)
        self.assertEqual(result, 1)
        self.assertIn("state=degraded", output)
        self.assertIn("degraded=yes", output)
        self.assertIn("open=2", output)  # the healthy repo survived
        self.assertIn("unavailable=b/two", output)

    def test_pull_request_all_repos_unavailable_is_not_zero_prs(self) -> None:
        # Every repo failing is "ci-hub/GitHub unavailable", never "no open PRs".
        with mock.patch.object(
            operational_health.pr_status, "DEFAULT_REPOS", ["a/one", "b/two"]
        ), mock.patch.object(
            operational_health.pr_status,
            "fetch_repo_status",
            side_effect=[
                pr_status_unavailable("a/one blocked"),
                pr_status_unavailable("b/two blocked"),
            ],
        ):
            result, output = self.capture(operational_health.pull_request_gate)
        self.assertEqual(result, 1)
        self.assertIn("state=unavailable", output)
        self.assertNotIn("state=ok", output)

    def test_pull_request_gate_bounds_each_repo_under_the_guillotine(self) -> None:
        # The gate MUST pass a per-repo timeout so it resolves before tick-hub's
        # 30s SubprocessGateRunner kills it (which would erase the reason).
        healthy = SimpleNamespace(
            open=0, red=0, green=0, pending=0, real_reds=0, outage_suspected=False
        )
        with mock.patch.object(
            operational_health.pr_status, "DEFAULT_REPOS", ["a/one"]
        ), mock.patch.object(
            operational_health.pr_status,
            "fetch_repo_status",
            return_value=healthy,
        ) as fetch:
            self.capture(operational_health.pull_request_gate)
        _, kwargs = fetch.call_args
        self.assertIn("timeout", kwargs)
        self.assertLessEqual(kwargs["timeout"] * 2, 30.0)

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

    def test_active_work_reconciles_all_five_classes(self) -> None:
        task = operational_health.TaskRecord
        agent = operational_health.AgentRecord
        report = operational_health.reconcile_active_work(
            [
                task("active", "Active", "alice", ()),
                task("await", "Awaiting", "alice", ("implemented",)),
                task("stale", "Stale", "", ()),
                task("orphan", "Orphan", "retired-worker", ()),
                task("mismatch", "Mismatch", "bob", ()),
            ],
            [
                agent("alice", "busy", "active"),
                agent("bob", "working", "different-task"),
                agent("offbook", "running", None),
                agent("retired-worker", "retired", "orphan"),
            ],
        )
        self.assertEqual(report.counts()["in_progress"], 5)
        self.assertEqual([item.id for item in report.actually_active], ["active"])
        self.assertEqual([item.id for item in report.awaiting_land], ["await"])
        self.assertEqual([item.id for item in report.stale], ["stale"])
        self.assertEqual([item.id for item in report.orphaned], ["orphan"])
        self.assertEqual([item.name for item in report.off_book], ["offbook"])
        self.assertEqual(len(report.misrouted), 2)
        self.assertGreater(report.actionable_count, 0)

    def test_active_work_resolves_thirteen_orc_titles_without_false_pairs(
        self,
    ) -> None:
        tasks = []
        agents = []
        for index in range(13):
            task_id = f"healthy_task_{index:02d}"
            title = (
                "Healthy short task"
                if index == 0
                else f"Healthy task {index:02d} -- a title longer than forty characters"
            )
            owner = f"worker-{index:02d}"
            tasks.append(operational_health.TaskRecord(task_id, title, owner, ()))
            agents.append(
                operational_health.AgentRecord(
                    owner,
                    "busy",
                    title if len(title) <= 40 else f"{title[:37]}...",
                )
            )

        report = operational_health.reconcile_active_work(tasks, agents)

        # The old two-way local_id/title comparison emitted 2 rows per agent.
        self.assertEqual(report.counts()["actually_active"], 13)
        self.assertEqual(report.counts()["misrouted"], 0)

    def test_active_work_still_reports_a_real_title_resolved_misroute(self) -> None:
        expected_title = "Expected task whose title is longer than forty characters"
        actual_title = "Different task whose title is longer than forty characters"
        report = operational_health.reconcile_active_work(
            [
                operational_health.TaskRecord(
                    "expected_task", expected_title, "worker", ()
                ),
                operational_health.TaskRecord("actual_task", actual_title, "", ()),
            ],
            [
                operational_health.AgentRecord(
                    "worker",
                    "busy",
                    f"{actual_title[:37]}...",
                ),
            ],
        )
        self.assertEqual(report.actually_active, ())
        self.assertEqual(report.counts()["misrouted"], 2)
        self.assertEqual({item.agent for item in report.misrouted}, {"worker"})
        self.assertIn(
            operational_health.Misroute(
                "worker",
                "expected_task",
                "owner-status=busy,owner-current-task=actual_task",
            ),
            report.misrouted,
        )
        self.assertIn(
            operational_health.Misroute(
                "worker",
                "actual_task",
                "task-owner=none",
            ),
            report.misrouted,
        )

    def test_active_work_refuses_an_ambiguous_title_prefix(self) -> None:
        shared_prefix = "A title prefix shared by two active tasks"
        display_title = f"{shared_prefix[:37]}..."
        report = operational_health.reconcile_active_work(
            [
                operational_health.TaskRecord(
                    "first", f"{shared_prefix} first", "worker", ()
                ),
                operational_health.TaskRecord(
                    "second", f"{shared_prefix} second", "other", ()
                ),
            ],
            [
                operational_health.AgentRecord("worker", "busy", display_title),
                operational_health.AgentRecord("other", "idle", None),
            ],
        )
        self.assertEqual(report.actually_active, ())
        self.assertIn(
            operational_health.Misroute(
                "worker",
                display_title,
                "current-task-title-is-ambiguous",
            ),
            report.misrouted,
        )

    def test_awaiting_land_alone_is_not_actionable(self) -> None:
        report = operational_health.reconcile_active_work(
            [
                operational_health.TaskRecord(
                    "await",
                    "Awaiting",
                    "worker",
                    ("implemented",),
                )
            ],
            [operational_health.AgentRecord("worker", "idle", None)],
        )
        self.assertEqual(report.actionable_count, 0)
        self.assertEqual(report.counts()["awaiting_land"], 1)
        self.assertEqual(report.counts()["actually_active"], 0)

    def test_agent_snapshot_cache_is_freshness_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agents.json"
            agents, captured_at = operational_health.load_agent_snapshot(
                '[{"name":"worker","status":"busy","current_task":"task"}]',
                snapshot_file=path,
                now=1000,
            )
            self.assertEqual(agents[0].current_task, "task")
            self.assertEqual(captured_at, 1000)
            cached, _ = operational_health.load_agent_snapshot(
                None,
                snapshot_file=path,
                max_age_secs=60,
                now=1050,
            )
            self.assertEqual(cached, agents)
            with self.assertRaisesRegex(RuntimeError, "agent-snapshot-stale"):
                operational_health.load_agent_snapshot(
                    None,
                    snapshot_file=path,
                    max_age_secs=60,
                    now=1061,
                )

    def test_cache_agent_snapshot_validates_before_persisting(self) -> None:
        snapshot = '[{"name":"worker","status":"busy","current_task":"task"}]'
        with mock.patch.object(
            operational_health,
            "_persist_agent_snapshot",
        ) as persist:
            result, output = self.capture(
                operational_health.cache_agent_snapshot,
                snapshot,
            )
        self.assertEqual(result, 0)
        self.assertIn("state=ok", output)
        self.assertIn("count=1", output)
        persist.assert_called_once()

    def test_taskgraph_query_parses_exact_tag_array(self) -> None:
        output = """task_json
---------------
{"id":"one","title":"One","owner":"worker","tags":["implemented"]}

(1 rows)
"""
        completed = SimpleNamespace(returncode=0, stdout=output, stderr="")
        with mock.patch.object(
            operational_health.subprocess,
            "run",
            return_value=completed,
        ):
            tasks = operational_health._taskgraph_in_progress()
        self.assertEqual(len(tasks), 1)
        self.assertTrue(tasks[0].implemented)

    def test_active_work_gate_emits_counts_and_items(self) -> None:
        tasks = (
            operational_health.TaskRecord("active", "Active", "worker", ()),
            operational_health.TaskRecord("stale", "Stale", "", ()),
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            operational_health,
            "_taskgraph_in_progress",
            return_value=tasks,
        ):
            result, output = self.capture(
                operational_health.active_work_gate,
                snapshot=(
                    '[{"name":"worker","status":"busy",' '"current_task":"active"}]'
                ),
                snapshot_file=Path(directory) / "agents.json",
                gate_output=True,
                now=1000,
            )
        self.assertEqual(result, 1)
        self.assertIn("state=drift", output)
        self.assertIn("actually_active=1", output)
        self.assertIn("stale=1", output)
        self.assertIn("detail=STALE stale", output)


if __name__ == "__main__":
    unittest.main()

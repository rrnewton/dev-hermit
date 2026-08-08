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

    def test_pull_request_gate_exposes_setup_only_no_result(self) -> None:
        status = SimpleNamespace(
            open=1,
            red=0,
            green=0,
            pending=1,
            real_reds=0,
            setup_only_no_result_checks=1,
            outage_suspected=False,
        )
        with mock.patch.object(
            operational_health.pr_status, "DEFAULT_REPOS", ["a/one"]
        ), mock.patch.object(
            operational_health.pr_status,
            "fetch_repo_status",
            return_value=status,
        ):
            result, output = self.capture(operational_health.pull_request_gate)
        self.assertEqual(result, 0)
        self.assertIn("state=ok", output)
        self.assertIn("setup_only_no_result_checks=1", output)
        self.assertIn("setup_only_no_result=1", output)

    def test_pull_request_gate_exposes_prerequisite_no_result(self) -> None:
        status = SimpleNamespace(
            open=1,
            red=0,
            green=0,
            pending=1,
            real_reds=0,
            setup_only_no_result_checks=1,
            prerequisite_no_result_checks=1,
            outage_suspected=False,
        )
        with mock.patch.object(
            operational_health.pr_status, "DEFAULT_REPOS", ["a/one"]
        ), mock.patch.object(
            operational_health.pr_status,
            "fetch_repo_status",
            return_value=status,
        ):
            result, output = self.capture(operational_health.pull_request_gate)
        self.assertEqual(result, 0)
        self.assertIn("prerequisite_no_result_checks=1", output)
        self.assertIn("prerequisite_no_result=1", output)

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

    def test_memory_gate_accepts_absent_optional_store_when_skills_are_clean(self) -> None:
        """A stock Codex/CI host has no Claude memory directory by design."""
        lint = (
            0,
            "active skills: 41  mapped memories: 0  in-sync: 0  "
            "problems: 0  warnings: 1\n",
            "",
        )
        scan = (
            0,
            "state=ok\nsummary=41 authoritative repository skills clean; "
            "0 optional local finding(s)\ncontradictions=0\ndrift=0\n",
            "",
        )
        with mock.patch.object(
            operational_health,
            "_run_tool",
            side_effect=[lint, scan],
        ):
            result, output = self.capture(operational_health.memory_skill_sync_gate)
        self.assertEqual(result, 0)
        self.assertIn("state=ok", output)
        self.assertIn("problems=0", output)
        self.assertIn("contradictions=0", output)

    def test_memory_gate_still_refuses_repository_skill_contradictions(self) -> None:
        lint = (0, "problems: 0\n", "")
        scan = (
            1,
            "state=contradiction\nsummary=one repository contradiction\n"
            "contradictions=1\ndrift=0\nACTION: reconcile repository skill\n",
            "",
        )
        with mock.patch.object(
            operational_health,
            "_run_tool",
            side_effect=[lint, scan],
        ):
            result, output = self.capture(operational_health.memory_skill_sync_gate)
        self.assertEqual(result, 1)
        self.assertIn("state=contradiction", output)
        self.assertIn("contradictions=1", output)
        self.assertIn("ACTION: reconcile repository skill", output)


if __name__ == "__main__":
    unittest.main()


class CloseOnImplementedLifecycleTest(unittest.TestCase):
    """The close-on-implemented lifecycle, asserted as code behaviour.

    Under this lifecycle an implemented task is CLOSED immediately and the
    landing debt is enumerated from CLOSED+implemented records by
    `drain-implemented-to-landed`. Before this, `awaiting_land` was derived
    from the IN_PROGRESS set, which reports ~zero once the lifecycle is
    followed -- the landing debt became invisible exactly when the policy
    started working.
    """

    @staticmethod
    def _task(tid, *, status="IN_PROGRESS", owner="", tags=(), landed=False):
        return operational_health.TaskRecord(
            id=tid, title=tid, owner=owner, tags=tuple(tags), status=status,
            landed=landed,
        )

    def fixture(self):
        """active-ready, implemented-unlanded, backlog, blocked, stale."""
        return [
            self._task("active-ready", owner="agent-a"),
            self._task("stale-unowned", owner=""),
            self._task("implemented-unlanded", status="CLOSED",
                       tags=("implemented",)),
            self._task("implemented-unlanded-2", status="CLOSED",
                       tags=("determinism", "implemented")),
            self._task("backlog-item", status="BACKLOG"),
            self._task("blocked-item", status="OPEN"),
        ]

    def report(self):
        agents = [operational_health.AgentRecord(
            name="agent-a", status="running", current_task="active-ready")]
        return operational_health.reconcile_active_work(self.fixture(), agents)

    def test_only_live_in_progress_work_reaches_the_actionable_queue(self):
        r = self.report()
        # BACKLOG/OPEN/CLOSED rows must not be treated as the live queue.
        self.assertEqual([t.id for t in r.in_progress],
                         ["active-ready", "stale-unowned"])
        self.assertEqual([t.id for t in r.owned_active], ["active-ready"])
        self.assertEqual([t.id for t in r.stale], ["stale-unowned"])

    def test_landing_monitor_still_sees_the_implemented_set(self):
        r = self.report()
        # THE REGRESSION THIS GUARDS: keyed off status, this would be empty.
        self.assertEqual(
            [t.id for t in r.awaiting_land],
            ["implemented-unlanded", "implemented-unlanded-2"],
        )
        self.assertEqual(r.counts()["awaiting_land"], 2)

    def test_awaiting_land_moves_in_BOTH_directions(self):
        """A queue depth that cannot fall is not a queue depth.

        The defect: `awaiting_landing` was a bare `return self.implemented`,
        with an entry condition and NO exit. Nothing removes the tag and no
        `landed` tag exists, so every landed task incremented the count
        forever — it grew monotonically with success and read 1718 while the
        real backlog was ~47. This asserts the property that was missing, in
        both directions, because only rising is the whole bug.
        """
        base = self.fixture()
        agents = [operational_health.AgentRecord(
            name="agent-a", status="running", current_task="active-ready")]

        def depth(tasks):
            return operational_health.reconcile_active_work(
                tasks, agents).counts()["awaiting_land"]

        start = depth(base)
        self.assertEqual(start, 2)

        # UP: tag one more task implemented -> the debt RISES.
        rose = base + [self._task("newly-implemented", status="CLOSED",
                                  tags=("implemented",))]
        self.assertEqual(depth(rose), start + 1,
                         "tagging implemented must increase the debt")

        # DOWN: that same task lands (gateway records CLOSURE-VERIFIED)
        # -> the debt FALLS back. This is the direction the old code could
        # never express.
        landed = base + [self._task("newly-implemented", status="CLOSED",
                                    tags=("implemented",), landed=True)]
        self.assertEqual(depth(landed), start,
                         "a proven-landed task must leave the debt")

        # And landing the whole original set drains it to zero.
        all_landed = [
            self._task(t.id, status=t.status, owner=t.owner, tags=t.tags,
                       landed=t.implemented)
            for t in base
        ]
        self.assertEqual(depth(all_landed), 0,
                         "the debt must be able to reach zero")

    def test_closed_status_alone_does_not_discharge_the_debt(self):
        """CLOSED is not landed, and must not be read as landed.

        Measured 2026-08-07: only 291 of 1718 implemented tasks carried a
        CLOSURE-VERIFIED proof, so 1427 were closed with no landing evidence.
        Exiting on status instead of proof would discharge the debt by
        assertion — the phantom-closure mode the gateway exists to prevent.
        """
        closed_unproven = [self._task("closed-no-proof", status="CLOSED",
                                      tags=("implemented",), landed=False)]
        r = operational_health.reconcile_active_work(closed_unproven, [])
        self.assertEqual([t.id for t in r.awaiting_land], ["closed-no-proof"])

    def test_lifecycle_violation_stays_a_distinct_signal(self):
        """The two metrics must not collapse into near-duplicates.

        Scoping awaiting_land to IN_PROGRESS (the other candidate fix) would
        have made it `implemented AND NOT closed`, which is exactly
        `lifecycle_violation`. Here an implemented+landed+open task is a
        lifecycle deviation but NOT landing debt, so the sets differ.
        """
        tasks = [self._task("open-implemented-landed", status="IN_PROGRESS",
                            owner="a", tags=("implemented",), landed=True)]
        r = operational_health.reconcile_active_work(tasks, [])
        self.assertEqual([t.id for t in r.lifecycle_violations],
                         ["open-implemented-landed"])
        self.assertEqual([t.id for t in r.awaiting_land], [])

    def test_closed_implemented_work_is_never_dispatchable(self):
        """Negative mutation: the landing debt must not leak into any set an
        idle agent could be assigned from, nor into the closure-candidate
        sets."""
        r = self.report()
        debt = {"implemented-unlanded", "implemented-unlanded-2"}
        for name in ("in_progress", "owned_active", "actually_active",
                     "stale", "orphaned"):
            leaked = debt & {t.id for t in getattr(r, name)}
            self.assertEqual(leaked, set(), f"landing debt leaked into {name}")

    def test_implemented_but_not_closed_is_reported_as_a_deviation(self):
        """An implemented row left nonterminal is invisible to the drain
        tracker while still occupying the live queue, so it is surfaced as a
        violation rather than silently absorbed."""
        tasks = self.fixture() + [
            self._task("implemented-but-open", status="IN_PROGRESS",
                       tags=("implemented",))
        ]
        r = operational_health.reconcile_active_work(tasks, [])
        self.assertEqual([t.id for t in r.lifecycle_violations],
                         ["implemented-but-open"])
        # It still counts as landing debt (tag-first), and still must not be
        # dispatchable as active work.
        self.assertIn("implemented-but-open", {t.id for t in r.awaiting_land})
        self.assertNotIn("implemented-but-open", {t.id for t in r.owned_active})
        self.assertNotIn("implemented-but-open", {t.id for t in r.stale})

    def test_conformant_graph_reports_zero_violations(self):
        """Positive control: the violation detector is not inert -- it fires
        above and reports zero here, on the same code path."""
        r = self.report()
        self.assertEqual(r.counts()["lifecycle_violations"], 0)

    def test_query_selects_both_populations(self):
        """The SQL must fetch IN_PROGRESS *and* implemented-at-any-status;
        fetching only IN_PROGRESS is what made the debt invisible."""
        src = Path(operational_health.__file__).read_text()
        self.assertIn("WHERE status = 'IN_PROGRESS'", src)
        self.assertIn("json_each(tasks.tags)", src)
        self.assertIn("json_each.value = 'implemented'", src)

    def test_awaiting_land_detail_is_bounded_and_states_its_residue(self):
        """The debt is now the full CLOSED+implemented population, so the
        detail line must not enumerate it -- and must not silently truncate."""
        tasks = [
            self._task(f"impl-{i:02d}", status="CLOSED", tags=("implemented",))
            for i in range(12)
        ]
        r = operational_health.reconcile_active_work(tasks, [])
        detail = operational_health._active_work_detail(r)
        shown = [d for d in detail if d.startswith("AWAITING-LAND impl-")]
        self.assertEqual(len(shown), 5)
        self.assertTrue(
            any("+7 more" in d for d in detail),
            f"residue must be stated explicitly, got: {detail}",
        )


class PrimarySnapshotDeferralTest(unittest.TestCase):
    """Time the deferral instead of paging on the first lost race.

    `primary_checkout` decides whether the snapshot CAN be published right now;
    this layer is the only one that can tell a race from a stuck snapshot, because
    only it persists across ticks. The threshold is on TIME, not on commit
    distance -- commit distance is the moving quantity that made this gate
    unsatisfiable in the first place.
    """

    def capture(self, **kwargs: object) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = operational_health.primary_snapshot_gate(**kwargs)  # type: ignore[arg-type]
        return int(result), output.getvalue()

    @contextlib.contextmanager
    def outcome(self, code: int):
        with mock.patch.object(
            operational_health.primary_checkout, "checkout_fresh", return_value=code
        ):
            yield

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "deferral.json"

    # ---- NEGATIVE: a lost race must not page --------------------------------

    def test_first_deferral_starts_the_clock_and_stays_quiet(self) -> None:
        with self.outcome(operational_health.primary_checkout.SNAPSHOT_DEFERRED):
            result, output = self.capture(state_path=self.state, now=1000.0)
        self.assertEqual(result, 0)
        self.assertIn("state=deferred", output)
        self.assertIn("deferred_mins=0", output)
        self.assertTrue(self.state.exists(), "the deferral clock was not persisted")

    def test_deferral_inside_the_budget_still_does_not_page(self) -> None:
        """59 minutes of losing races is still a race on a box this busy."""
        with self.outcome(operational_health.primary_checkout.SNAPSHOT_DEFERRED):
            self.capture(state_path=self.state, now=1000.0)
            result, output = self.capture(state_path=self.state, now=1000.0 + 59 * 60)
        self.assertEqual(result, 0)
        self.assertIn("state=deferred", output)
        self.assertIn("deferred_mins=59.0", output)

    # ---- POSITIVE: a stuck snapshot must page -------------------------------

    def test_deferral_past_the_budget_pages(self) -> None:
        """Twelve consecutive lost ticks is not luck; something is holding it down."""
        with self.outcome(operational_health.primary_checkout.SNAPSHOT_DEFERRED):
            self.capture(state_path=self.state, now=1000.0)
            result, output = self.capture(state_path=self.state, now=1000.0 + 61 * 60)
        self.assertEqual(result, 1)
        self.assertIn("state=blocked", output)
        self.assertIn("no longer a lost race", output)

    def test_a_real_block_pages_immediately_without_waiting_out_the_budget(self) -> None:
        """A dirty primary is not a moving reference and gets no grace period."""
        with self.outcome(operational_health.primary_checkout.SNAPSHOT_BLOCKED):
            result, output = self.capture(state_path=self.state, now=1000.0)
        self.assertEqual(result, 1)
        self.assertIn("state=blocked", output)

    # ---- the clock must be honest ------------------------------------------

    def test_a_successful_publish_clears_the_clock(self) -> None:
        """Otherwise an old deferral ages into a page after the problem is gone."""
        with self.outcome(operational_health.primary_checkout.SNAPSHOT_DEFERRED):
            self.capture(state_path=self.state, now=1000.0)
        with self.outcome(operational_health.primary_checkout.SNAPSHOT_PUBLISHED):
            result, output = self.capture(state_path=self.state, now=1000.0 + 10 * 60)
        self.assertEqual(result, 0)
        self.assertIn("state=ok", output)
        self.assertFalse(self.state.exists(), "the deferral clock survived a success")

        # And a fresh deferral after that starts from zero, not from the old clock.
        with self.outcome(operational_health.primary_checkout.SNAPSHOT_DEFERRED):
            result, output = self.capture(state_path=self.state, now=1000.0 + 20 * 60)
        self.assertEqual(result, 0)
        self.assertIn("deferred_mins=0", output)

    def test_a_hard_block_also_clears_the_clock(self) -> None:
        """A block is reported on its own terms; it must not inherit an aged clock
        and then page twice for two different reasons."""
        with self.outcome(operational_health.primary_checkout.SNAPSHOT_DEFERRED):
            self.capture(state_path=self.state, now=1000.0)
        with self.outcome(operational_health.primary_checkout.SNAPSHOT_BLOCKED):
            self.capture(state_path=self.state, now=1000.0 + 5 * 60)
        self.assertFalse(self.state.exists())

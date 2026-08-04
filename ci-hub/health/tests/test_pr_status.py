#!/usr/bin/env python3
"""Tests for the dev-hermit open-PR health tool (gh engine + planner adapter)."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pr_status


def _rollup(*states: tuple[str, str]) -> list[dict]:
    """Build a statusCheckRollup list; each (status, conclusion) is a CheckRun."""
    return [{"status": s, "conclusion": c} for s, c in states]


def _pr(
    number: int,
    rollup,
    *,
    mergeable="MERGEABLE",
    merge_state="CLEAN",
    draft=False,
    labels=(),
):
    return {
        "number": number,
        "title": f"pr {number}",
        "isDraft": draft,
        "mergeable": mergeable,
        "mergeStateStatus": merge_state,
        "labels": [{"name": label} for label in labels],
        "statusCheckRollup": rollup,
    }


class GhEngineClassificationTests(unittest.TestCase):
    def test_green_red_pending_counts(self) -> None:
        raw = [
            _pr(1, _rollup(("COMPLETED", "SUCCESS"))),
            _pr(2, _rollup(("COMPLETED", "FAILURE")), mergeable="MERGEABLE",
                merge_state="BLOCKED"),
            _pr(3, _rollup(("IN_PROGRESS", ""))),
            _pr(4, []),  # no checks yet => pending
        ]
        status = pr_status._classify_gh_prs("rrnewton/hermit", raw)
        self.assertEqual(status.open, 4)
        self.assertEqual(status.green, 1)
        self.assertEqual(status.red, 1)
        self.assertEqual(status.pending, 2)
        self.assertEqual(status.real_reds, 1)

    def test_cancelled_check_is_pending_not_red(self) -> None:
        # Regression (task cancelled-run-classified-as-red): a cancelled check is a
        # hole to re-run, not a product failure. It must reduce to pending.
        self.assertEqual(
            pr_status._rollup_ci_state(_rollup(("COMPLETED", "CANCELLED"))),
            "pending",
        )
        self.assertEqual(
            pr_status._rollup_ci_state(_rollup(("COMPLETED", "ACTION_REQUIRED"))),
            "pending",
        )

    def test_real_failure_still_red(self) -> None:
        self.assertEqual(
            pr_status._rollup_ci_state(_rollup(("COMPLETED", "FAILURE"))), "red"
        )

    def test_drafts_excluded(self) -> None:
        raw = [_pr(1, _rollup(("COMPLETED", "SUCCESS")), draft=True)]
        status = pr_status._classify_gh_prs("rrnewton/hermit", raw)
        self.assertEqual(status.open, 0)

    def test_conflicting_red_is_stale_base_not_real(self) -> None:
        raw = [_pr(1, _rollup(("COMPLETED", "FAILURE")),
                   mergeable="CONFLICTING", merge_state="DIRTY")]
        status = pr_status._classify_gh_prs("rrnewton/hermit", raw)
        self.assertEqual(status.red, 1)
        self.assertEqual(status.real_reds, 0)
        self.assertEqual(status.prs[0]["red_class"], "stale-base")

    def test_unknown_mergeability_red_is_undetermined_not_real(self) -> None:
        # The lazy-mergeability flap: a cold query returns UNKNOWN; such reds must
        # NOT inflate real_reds or trip the outage alarm.
        raw = [
            _pr(i, _rollup(("COMPLETED", "FAILURE")),
                mergeable="UNKNOWN", merge_state="UNKNOWN")
            for i in range(10)
        ]
        status = pr_status._classify_gh_prs("rrnewton/hermit", raw)
        self.assertEqual(status.red, 10)
        self.assertEqual(status.real_reds, 0)
        self.assertEqual(status.undetermined_reds, 10)
        self.assertFalse(status.outage_suspected)
        self.assertFalse(status.unhealthy)

    def test_outage_only_from_known_real_reds(self) -> None:
        raw = [
            _pr(i, _rollup(("COMPLETED", "FAILURE")),
                mergeable="MERGEABLE", merge_state="BLOCKED")
            for i in range(5)
        ]
        status = pr_status._classify_gh_prs("rrnewton/hermit", raw)
        self.assertEqual(status.real_reds, 5)
        self.assertTrue(status.outage_suspected)
        self.assertTrue(status.unhealthy)


class ReviewProtocolClassificationTests(unittest.TestCase):
    def classify(self, *labels: str, draft: bool = False):
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit",
            [_pr(12, [], draft=draft, labels=labels)],
        )
        self.assertEqual(len(status.review_protocol), 1)
        return status.review_protocol[0]

    def test_round_one_and_current_approvals_are_complete(self) -> None:
        audit = self.classify(
            "post-facto-human-review",
            "adversarial-review-codex1",
            "adversarial-review-claude1",
            "passed-review-codex",
            "passed-review-claude",
        )
        self.assertTrue(audit.complete)
        self.assertEqual(audit.review_rounds, "complete")
        self.assertEqual(audit.current_approvals, "complete")

    def test_later_protocol_rounds_are_valid(self) -> None:
        audit = self.classify(
            "post-facto-human-review",
            "adversarial-review-codex4",
            "adversarial-review-claude3",
            "passed-review-codex",
            "passed-review-claude",
        )
        self.assertTrue(audit.complete)
        self.assertEqual(audit.codex_rounds, (4,))
        self.assertEqual(audit.claude_rounds, (3,))

    def test_one_sided_review_is_distinct_from_current_approval(self) -> None:
        audit = self.classify(
            "post-facto-human-review",
            "adversarial-review-claude1",
            "passed-review-claude",
        )
        self.assertEqual(audit.review_rounds, "partial")
        self.assertEqual(audit.current_approvals, "partial")
        self.assertIn("review-round-codex", audit.missing)
        self.assertIn("current-approval-codex", audit.missing)

    def test_invalid_or_bare_round_labels_do_not_fake_review(self) -> None:
        audit = self.classify(
            "post-facto-human-review",
            "adversarial-review-codex",
            "adversarial-review-claude0",
            "adversarial-review-codex5",
            "adversarial-review-claude10",
        )
        self.assertEqual(audit.review_rounds, "missing")
        self.assertEqual(len(audit.invalid_labels), 4)

    def test_suffixed_approval_does_not_mean_current_head_approved(self) -> None:
        audit = self.classify(
            "post-facto-human-review",
            "adversarial-review-codex2",
            "adversarial-review-claude2",
            "passed-review-codex2",
            "passed-review-claude2",
        )
        self.assertEqual(audit.review_rounds, "complete")
        self.assertEqual(audit.current_approvals, "missing")
        self.assertFalse(audit.complete)
        self.assertEqual(len(audit.invalid_labels), 2)

    def test_drafts_stay_out_of_ci_counts_but_in_review_audit(self) -> None:
        audit = self.classify("post-facto-human-review", draft=True)
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit",
            [_pr(12, [], draft=True, labels=("post-facto-human-review",))],
        )
        self.assertTrue(audit.draft)
        self.assertEqual(status.open, 0)
        self.assertEqual(len(status.review_protocol), 1)


class MechanismOverlapTests(unittest.TestCase):
    def test_same_mechanism_on_two_open_prs_is_surfaced(self) -> None:
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit",
            [
                _pr(1567, [], labels=("mechanism:cancel-in-progress",)),
                _pr(
                    1575,
                    [],
                    draft=True,
                    labels=("mechanism:cancel-in-progress",),
                ),
            ],
        )
        self.assertEqual(len(status.mechanism_overlaps), 1)
        overlap = status.mechanism_overlaps[0]
        self.assertEqual(overlap.mechanism, "mechanism:cancel-in-progress")
        self.assertEqual([pr.pr for pr in overlap.prs], [1567, 1575])
        self.assertTrue(overlap.prs[1].draft)

    def test_distinct_or_singleton_mechanisms_do_not_warn(self) -> None:
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit",
            [
                _pr(1, [], labels=("mechanism:CI_DAG_JOBS",)),
                _pr(2, [], labels=("mechanism:locally-validated",)),
                _pr(3, [], labels=("mechanism:",)),
            ],
        )
        self.assertEqual(status.mechanism_overlaps, ())

    def test_report_names_every_pr_in_the_overlap(self) -> None:
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit",
            [
                _pr(12, [], labels=("mechanism:locally-validated",)),
                _pr(34, [], labels=("mechanism:locally-validated",)),
            ],
        )
        report = pr_status.render_report([status], warn_threshold=10, engine="gh")
        self.assertIn("Mechanism overlaps: 1", report)
        self.assertIn("mechanism:locally-validated", report)
        self.assertIn("#12", report)
        self.assertIn("#34", report)


class GhEngineLoudFailureTests(unittest.TestCase):
    """No output + zero exit must never look like 'no PRs'."""

    @mock.patch("pr_status.subprocess.run")
    def test_empty_stdout_is_unavailable_not_zero_prs(self, run: mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with self.assertRaisesRegex(pr_status.RepoUnavailable, "EMPTY output"):
            pr_status.fetch_repo_status_gh("rrnewton/hermit", net_wrapper=[])

    @mock.patch("pr_status.subprocess.run")
    def test_bpfjailer_block_is_actionable_unavailable(self, run: mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="An action was blocked on this server based on a security policy!",
        )
        with self.assertRaisesRegex(pr_status.RepoUnavailable, "BpfJailer"):
            pr_status.fetch_repo_status_gh("rrnewton/hermit", net_wrapper=[])
        run.assert_called_once()  # a policy block is not retried

    @mock.patch("pr_status.subprocess.run")
    def test_unreachable_network_is_unavailable(self, run: mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr='dial tcp: connect: network is unreachable',
        )
        with self.assertRaises(pr_status.RepoUnavailable):
            pr_status.fetch_repo_status_gh("rrnewton/hermit", net_wrapper=[])

    @mock.patch("pr_status.subprocess.run")
    def test_non_json_is_unavailable(self, run: mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json", stderr=""
        )
        with self.assertRaises(pr_status.RepoUnavailable):
            pr_status.fetch_repo_status_gh("rrnewton/hermit", net_wrapper=[])

    @mock.patch("pr_status.subprocess.run")
    def test_timeout_is_unavailable_not_hang(self, run: mock.Mock) -> None:
        run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=1.0)
        with self.assertRaises(pr_status.RepoUnavailable):
            pr_status.fetch_repo_status_gh(
                "rrnewton/hermit", net_wrapper=[], timeout=1.0
            )
        run.assert_called_once()

    @mock.patch("pr_status.time.sleep")
    @mock.patch("pr_status.subprocess.run")
    def test_transient_stream_error_retries(self, run, sleep) -> None:
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=1, stdout="",
                stderr="stream error: stream ID 1; CANCEL",
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout="[]", stderr=""
            ),
        ]
        status = pr_status.fetch_repo_status_gh("rrnewton/hermit", net_wrapper=[])
        self.assertEqual(status.open, 0)  # legit empty list == 0 PRs (query OK)
        sleep.assert_called_once()

    def test_net_wrapper_default_prefers_with_proxy(self) -> None:
        with mock.patch("pr_status.shutil.which", return_value="/usr/bin/with-proxy"):
            self.assertEqual(pr_status.resolve_net_wrapper(None), ["with-proxy"])

    def test_net_wrapper_missing_named_is_error(self) -> None:
        with mock.patch("pr_status.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                pr_status.resolve_net_wrapper("nope-not-real")

    def test_net_wrapper_empty_disables(self) -> None:
        self.assertEqual(pr_status.resolve_net_wrapper(""), [])


class PlannerAdapterTests(unittest.TestCase):
    def test_command_uses_pinned_agent_utils_front_door(self) -> None:
        command = pr_status.planner_command("rrnewton/hermit", 7)
        self.assertEqual(command[1:3], ["pr-landing-planner", "status"])
        self.assertIn("--net-wrapper", command)
        self.assertIn("with-proxy", command)
        self.assertIn("--format", command)
        self.assertIn("json", command)

    def test_planning_runs_use_real_merge_tree_conflict_detection(self) -> None:
        # The merge-tree engine is ON for planning runs (the opt-in `--engine
        # planner` path). It was previously pinned to `file-overlap` on an
        # unmeasured "expensive fan-out" theory; the real cost was the fetch,
        # not the analysis (see planner_command). Guard the flip so it cannot
        # silently rot back to the cheap fallback.
        command = pr_status.planner_command("rrnewton/hermit", 7)
        idx = command.index("--conflict-detector")
        self.assertEqual(command[idx + 1], "merge-tree")
        self.assertNotIn("file-overlap", command)

    @mock.patch("pr_status.subprocess.run")
    def test_fetch_uses_planner_schema_without_reimplementing_ci(
        self, run: mock.Mock
    ) -> None:
        payload = {
            "summary": {
                "open": 3, "green": 1, "red": 1, "pending": 1,
                "real_reds": 1, "outage_suspected": False,
            },
            "prs": [{"pr": 12, "ci": "red", "red_class": "real", "title": "fix"}],
        }
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr=""
        )
        status = pr_status.fetch_repo_status_planner("rrnewton/hermit")
        self.assertEqual(status.open, 3)
        self.assertTrue(status.unhealthy)
        self.assertEqual(status.prs[0]["red_class"], "real")

    @mock.patch("pr_status.subprocess.run")
    def test_planner_bpfjailer_block_is_actionable(self, run: mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="blocked on this server based on a security policy",
        )
        with self.assertRaisesRegex(pr_status.RepoUnavailable, "BpfJailer"):
            pr_status.fetch_repo_status_planner("rrnewton/hermit")

    @mock.patch("pr_status.subprocess.run")
    def test_timeout_yields_unavailable_not_hang(self, run: mock.Mock) -> None:
        run.side_effect = subprocess.TimeoutExpired(cmd="planner", timeout=1.0)
        with self.assertRaises(pr_status.RepoUnavailable):
            pr_status.fetch_repo_status_planner("rrnewton/hermit", timeout=1.0)
        run.assert_called_once()  # a timeout is terminal, not retried


class OrchestrationTests(unittest.TestCase):
    @mock.patch("pr_status.subprocess.run")
    def test_collect_records_partial_result_on_timeout(self, run: mock.Mock) -> None:
        run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=1.0)
        statuses = pr_status.collect_statuses(
            ["rrnewton/hermit"],
            warn_threshold=10,
            engine="gh",
            net_wrapper=[],
            per_repo_timeout=1.0,
            overall_deadline=5.0,
        )
        self.assertEqual(len(statuses), 1)
        self.assertFalse(statuses[0].available)
        self.assertFalse(statuses[0].unhealthy)

    @mock.patch("pr_status.time.sleep")
    @mock.patch("pr_status.subprocess.run")
    def test_full_query_504_still_surfaces_mechanism_overlap(
        self, run: mock.Mock, sleep: mock.Mock
    ) -> None:
        unavailable = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="HTTP 504: Gateway Timeout"
        )
        labels_only = [
            _pr(1567, [], labels=("mechanism:cancel-in-progress",)),
            _pr(
                1575,
                [],
                draft=True,
                labels=("mechanism:cancel-in-progress",),
            ),
        ]
        run.side_effect = [
            unavailable,
            unavailable,
            unavailable,
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(labels_only), stderr=""
            ),
        ]
        statuses = pr_status.collect_statuses(
            ["rrnewton/hermit"],
            warn_threshold=10,
            engine="gh",
            net_wrapper=[],
            per_repo_timeout=30.0,
            overall_deadline=60.0,
        )
        self.assertFalse(statuses[0].available)
        self.assertEqual(len(statuses[0].mechanism_overlaps), 1)
        report = pr_status.render_report(statuses, warn_threshold=10, engine="gh")
        self.assertIn("UNAVAILABLE", report)
        self.assertIn("mechanism:cancel-in-progress", report)
        self.assertIn("#1567", report)
        self.assertIn("#1575", report)
        self.assertEqual(run.call_count, 4)
        self.assertEqual(sleep.call_count, 2)

    def test_collect_marks_unavailable_when_deadline_exhausted(self) -> None:
        statuses = pr_status.collect_statuses(
            ["rrnewton/hermit", "rrnewton/reverie"],
            warn_threshold=10,
            engine="gh",
            net_wrapper=[],
            per_repo_timeout=300.0,
            overall_deadline=-1.0,
        )
        self.assertEqual(len(statuses), 2)
        self.assertTrue(all(not status.available for status in statuses))
        self.assertIn("deadline", statuses[0].reason)


class RenderTests(unittest.TestCase):
    def test_render_degraded_reports_partial(self) -> None:
        available = pr_status.RepoStatus(
            repo="rrnewton/reverie", open=1, green=1, red=0, pending=0,
            real_reds=0, outage_suspected=False, prs=(),
        )
        unavailable = pr_status._unavailable("rrnewton/hermit", "gh pr list failed")
        report = pr_status.render_report(
            [available, unavailable], warn_threshold=10, engine="gh"
        )
        self.assertIn("DEGRADED", report)
        self.assertIn("UNAVAILABLE", report)
        self.assertIn("PARTIAL RESULT", report)

    def test_render_distinguishes_benign_red_from_unhealthy(self) -> None:
        status = pr_status.RepoStatus(
            repo="rrnewton/hermit", open=2, green=1, red=1, pending=0,
            real_reds=0, outage_suspected=False, prs=(),
        )
        report = pr_status.render_report([status], warn_threshold=10, engine="gh")
        self.assertIn("CI health: HEALTHY", report)
        self.assertIn("red=1", report)

    def test_render_surfaces_undetermined_caution(self) -> None:
        status = pr_status.RepoStatus(
            repo="rrnewton/hermit", open=3, green=0, red=3, pending=0,
            real_reds=0, undetermined_reds=3, outage_suspected=False, prs=(),
        )
        report = pr_status.render_report([status], warn_threshold=10, engine="gh")
        self.assertIn("undetermined_reds=3", report)
        self.assertIn("CAUTION", report)

    def test_render_separates_review_history_from_current_approval(self) -> None:
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit",
            [
                _pr(
                    12,
                    [],
                    labels=(
                        "post-facto-human-review",
                        "adversarial-review-codex2",
                        "adversarial-review-claude3",
                        "passed-review-claude",
                    ),
                )
            ],
        )
        report = pr_status.render_report([status], warn_threshold=10, engine="gh")
        self.assertIn("dual_review=1", report)
        self.assertIn("current_dual_approval=0", report)
        self.assertIn("approval=partial", report)
        self.assertIn("missing=current-approval-codex", report)


if __name__ == "__main__":
    unittest.main()

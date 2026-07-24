#!/usr/bin/env python3
"""Tests for scripts/pr_status.py."""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from scripts import pr_status


def pull_request(
    *,
    repo: str = "rrnewton/hermit",
    number: int = 1,
    human_review: bool = False,
    ci_status: str = "green",
    draft: bool = False,
) -> pr_status.PullRequest:
    labels = frozenset((pr_status.HUMAN_REVIEW_LABEL,)) if human_review else frozenset()
    return pr_status.PullRequest(
        repo=repo,
        number=number,
        title=f"PR {number}",
        url=f"https://example.test/{repo}/{number}",
        is_draft=draft,
        labels=labels,
        ci_status=ci_status,
    )


class CiRollupTests(unittest.TestCase):
    def test_no_checks_is_none(self) -> None:
        self.assertEqual(pr_status.classify_ci_rollup([]), "none")
        self.assertEqual(pr_status.classify_ci_rollup(None), "none")

    def test_completed_successes_are_green(self) -> None:
        checks = [
            {"status": "COMPLETED", "conclusion": "SUCCESS"},
            {"status": "COMPLETED", "conclusion": "SKIPPED"},
        ]
        self.assertEqual(pr_status.classify_ci_rollup(checks), "green")

    def test_any_red_conclusion_wins_over_pending(self) -> None:
        checks = [
            {"status": "IN_PROGRESS", "conclusion": ""},
            {"status": "COMPLETED", "conclusion": "FAILURE"},
        ]
        self.assertEqual(pr_status.classify_ci_rollup(checks), "red")

    def test_incomplete_check_is_pending(self) -> None:
        checks = [{"status": "QUEUED", "conclusion": ""}]
        self.assertEqual(pr_status.classify_ci_rollup(checks), "pending")


class PullRequestTests(unittest.TestCase):
    def test_human_review_label_blocks_pr(self) -> None:
        raw = {
            "number": 12,
            "title": "  Review   this ",
            "url": "https://example.test/12",
            "isDraft": True,
            "labels": [{"name": "human-review"}, {"name": "backend"}],
            "statusCheckRollup": [],
        }
        pr = pr_status.parse_pull_request("rrnewton/reverie", raw)
        self.assertTrue(pr.needs_human_review)
        self.assertEqual(pr.title, "Review this")
        self.assertEqual(pr.ci_status, "none")

    @mock.patch("scripts.pr_status.subprocess.run")
    def test_fetch_always_uses_with_proxy_gh(self, run: mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="[]",
            stderr="",
        )
        self.assertEqual(pr_status.fetch_open_prs("rrnewton/hermit"), [])
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["with-proxy", "gh"])
        self.assertIn("statusCheckRollup", command[-1])


class ReportTests(unittest.TestCase):
    def test_report_categorizes_and_counts(self) -> None:
        prs = [
            pull_request(number=3, human_review=True, ci_status="green"),
            pull_request(number=2, ci_status="red", draft=True),
            pull_request(repo="rrnewton/reverie", number=1, ci_status="pending"),
        ]
        report = pr_status.render_report(prs, warn_threshold=10)
        self.assertIn("Human review (1)", report)
        self.assertIn("Free to land: no human-review label (2)", report)
        self.assertIn("total open:    3", report)
        self.assertIn("human-blocked: 1", report)
        self.assertIn("free-to-land:  2", report)
        self.assertIn("CI-failing:    1", report)
        self.assertIn("ci=red", report)
        self.assertIn("draft=yes", report)
        self.assertNotIn("WARNING:", report)

    def test_warning_is_strictly_above_threshold(self) -> None:
        at_threshold = [pull_request(number=n) for n in range(1, 11)]
        self.assertNotIn(
            "WARNING:",
            pr_status.render_report(at_threshold, warn_threshold=10),
        )

        above_threshold = at_threshold + [pull_request(number=11)]
        report = pr_status.render_report(above_threshold, warn_threshold=10)
        self.assertIn("WARNING: 11 free-to-land PRs exceeds the 10 PR threshold", report)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""An unavailable repo must be attributed to the cause that ACTUALLY fired.

THE BUG THIS EXISTS FOR. `ci-hub pr-status` printed, for every kind of failure:

    PARTIAL RESULT: 1 of 2 repo(s) could not be queried within the time budget

Measured 2026-08-07: hermit came back UNAVAILABLE on 2 of 5 consecutive lander
polls, both times with gh's own Go-side ``unexpected end of JSON input``. The
per-repo budget was 300s against a 5.0s query -- the budget was nowhere near
binding. Anyone acting on the printed message would raise
CI_HUB_PR_STATUS_TIMEOUT and observe no change, because the timeout was never
the constraint.

Only 2 of the 7 RepoUnavailable raise sites are budget exhaustion. Every test
below is bracketed BOTH WAYS: the qualifying text must classify to its cause,
and a text from a different cause must NOT reach it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pr_status


REPO = "rrnewton/hermit"

# The EXACT strings the seven raise sites emit, so these tests fail if a site is
# reworded out from under the classifier rather than silently reclassifying.
SITE_TEXTS = {
    "budget_exhausted": f"{REPO}: time budget exhausted before gh attempt 2",
    "gh_hang": f"{REPO}: `gh pr list` exceeded 300s (proxy stall or gh hang)",
    "bpfjailer": (
        f"{REPO}: gh/git blocked by BpfJailer security policy (FILE_OPEN); "
        "ensure the call is proxied. detail: FILE_OPEN denied"
    ),
    "gh_failed_truncated": (
        f"{REPO}: `gh pr list` failed: unexpected end of JSON input"
    ),
    "empty_output": (
        f"{REPO}: `gh pr list` returned exit 0 but EMPTY output; refusing to "
        "report 0 open PRs from an empty response"
    ),
    "non_json": f"{REPO}: gh returned non-JSON: <html>502 Bad Gateway</html>",
    "bad_schema": f"{REPO}: gh returned an unexpected schema (not a list)",
}

EXPECTED_CAUSE = {
    "budget_exhausted": pr_status.CAUSE_TIMEOUT,
    "gh_hang": pr_status.CAUSE_TIMEOUT,
    "bpfjailer": pr_status.CAUSE_BLOCKED,
    "gh_failed_truncated": pr_status.CAUSE_MALFORMED,
    "empty_output": pr_status.CAUSE_MALFORMED,
    "non_json": pr_status.CAUSE_MALFORMED,
    "bad_schema": pr_status.CAUSE_MALFORMED,
}


class ClassifyCause(unittest.TestCase):
    def test_every_raise_site_classifies_to_its_own_cause(self):
        """Positive direction, denominator 7: all seven sites, correct cause."""
        for key, text in SITE_TEXTS.items():
            with self.subTest(site=key):
                self.assertEqual(
                    pr_status.classify_unavailable_reason(text),
                    EXPECTED_CAUSE[key],
                )

    def test_only_two_of_seven_sites_are_timeout(self):
        """The whole defect was reporting 7/7 as timeout. Pin the real split."""
        causes = [
            pr_status.classify_unavailable_reason(t) for t in SITE_TEXTS.values()
        ]
        self.assertEqual(causes.count(pr_status.CAUSE_TIMEOUT), 2)
        self.assertEqual(causes.count(pr_status.CAUSE_MALFORMED), 4)
        self.assertEqual(causes.count(pr_status.CAUSE_BLOCKED), 1)

    def test_malformed_is_never_reported_as_timeout(self):
        """Negative direction: the misattribution must be refused."""
        for key in ("gh_failed_truncated", "empty_output", "non_json", "bad_schema"):
            with self.subTest(site=key):
                self.assertNotEqual(
                    pr_status.classify_unavailable_reason(SITE_TEXTS[key]),
                    pr_status.CAUSE_TIMEOUT,
                )

    def test_timeout_is_never_reported_as_malformed(self):
        """Negative direction, the other way: don't overcorrect."""
        for key in ("budget_exhausted", "gh_hang"):
            with self.subTest(site=key):
                self.assertNotEqual(
                    pr_status.classify_unavailable_reason(SITE_TEXTS[key]),
                    pr_status.CAUSE_MALFORMED,
                )

    def test_rate_limit_exceeded_is_not_a_timeout(self):
        """A bare 'exceeded' marker would swallow a quota fault as a budget
        fault and point the reader at the wrong knob again."""
        text = f"{REPO}: `gh pr list` failed: API rate limit exceeded"
        self.assertNotEqual(
            pr_status.classify_unavailable_reason(text), pr_status.CAUSE_TIMEOUT
        )

    def test_unknown_text_falls_back_without_asserting_a_cause(self):
        self.assertEqual(
            pr_status.classify_unavailable_reason("something nobody predicted"),
            pr_status.CAUSE_QUERY_FAILED,
        )
        self.assertEqual(
            pr_status.classify_unavailable_reason(""), pr_status.CAUSE_QUERY_FAILED
        )


class RetryCoverage(unittest.TestCase):
    """The retry loop already existed; the observed fault class was not in it."""

    @staticmethod
    def _retryable(detail: str) -> bool:
        # Mirrors the predicate at the gh call site exactly.
        return any(
            m.lower() in detail.lower() for m in pr_status._RETRYABLE_MARKERS
        )

    def test_truncated_response_is_now_retryable(self):
        """Positive: the exact production failure, twice in five polls."""
        self.assertTrue(self._retryable("unexpected end of JSON input"))

    def test_transient_markers_still_retryable(self):
        for detail in ("stream error", "504 Gateway", "connection reset"):
            with self.subTest(detail=detail):
                self.assertTrue(self._retryable(detail))

    def test_deterministic_faults_are_still_not_retried(self):
        """Negative: retrying these is pure waste; they cannot self-correct."""
        for detail in (
            "could not resolve to a Repository with the name",
            "gh: command not found",
            "GraphQL: Resource not accessible by integration",
        ):
            with self.subTest(detail=detail):
                self.assertFalse(self._retryable(detail))


class PartialResultLine(unittest.TestCase):
    """The rendered summary is what a human acts on, so assert on IT."""

    def _render(self, reason: str) -> str:
        statuses = [
            pr_status._unavailable(REPO, reason),
            pr_status.RepoStatus(
                repo="rrnewton/reverie",
                open=3,
                green=3,
                red=0,
                pending=0,
                real_reds=0,
                outage_suspected=False,
                prs=(),
            ),
        ]
        return pr_status.render_report(statuses, warn_threshold=10, engine="gh")

    def test_truncated_response_does_not_blame_the_time_budget(self):
        out = self._render(SITE_TEXTS["gh_failed_truncated"])
        self.assertIn("PARTIAL RESULT", out)
        self.assertIn(pr_status.CAUSE_MALFORMED, out)
        # The regression itself:
        self.assertNotIn("within the time budget", out)

    def test_truncated_response_warns_the_timeout_knob_will_not_help(self):
        out = self._render(SITE_TEXTS["gh_failed_truncated"])
        self.assertIn("will NOT help", out)

    def test_real_timeout_still_points_at_the_timeout_knob(self):
        """Positive control: the advice must still FIRE for a real timeout."""
        out = self._render(SITE_TEXTS["gh_hang"])
        self.assertIn(pr_status.CAUSE_TIMEOUT, out)
        self.assertIn("CI_HUB_PR_STATUS_TIMEOUT", out)

    def test_blocked_points_at_the_proxy_not_the_budget(self):
        out = self._render(SITE_TEXTS["bpfjailer"])
        self.assertIn(pr_status.CAUSE_BLOCKED, out)
        self.assertIn("with-proxy", out)

    def test_partial_result_still_refuses_to_claim_zero_prs(self):
        """Do not 'fix' the correct fail-closed behaviour while fixing the text."""
        out = self._render(SITE_TEXTS["gh_failed_truncated"])
        self.assertIn("NOT a claim that they have no PRs", out)


if __name__ == "__main__":
    unittest.main()

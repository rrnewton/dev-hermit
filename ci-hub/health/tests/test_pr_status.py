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
    head="a" * 40,
):
    return {
        "number": number,
        "title": f"pr {number}",
        "isDraft": draft,
        "mergeable": mergeable,
        "mergeStateStatus": merge_state,
        "labels": [{"name": label} for label in labels],
        "statusCheckRollup": rollup,
        "headRefOid": head,
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

    def test_no_result_checks_block_green_without_becoming_red(self) -> None:
        for conclusion in ("CANCELLED", "SKIPPED", "NEUTRAL", "STALE", "FUTURE"):
            with self.subTest(conclusion=conclusion):
                self.assertEqual(
                    pr_status._rollup_ci_state(_rollup(("COMPLETED", conclusion))),
                    "pending",
                )

    def test_real_failure_still_red(self) -> None:
        self.assertEqual(
            pr_status._rollup_ci_state(_rollup(("COMPLETED", "FAILURE"))), "red"
        )

    def test_real_red_failing_only_merge_gate_is_gate_not_product(self) -> None:
        # A mergeable PR whose ONLY failing check is the receipt meta-check is a
        # real-red, but a landing-gate red — not product breakage.
        raw = [
            _pr(
                10,
                [
                    {"name": "merge-gate-v2", "status": "COMPLETED",
                     "conclusion": "FAILURE"},
                    {"name": "Regular tests (GitHub-hosted)", "status": "COMPLETED",
                     "conclusion": "SUCCESS"},
                ],
            ),
        ]
        status = pr_status._classify_gh_prs("rrnewton/hermit", raw)
        self.assertEqual(status.real_reds, 1)
        self.assertEqual(status.gate_reds, 1)
        self.assertEqual(status.product_reds, 0)
        self.assertEqual(status.prs[0]["real_red_kind"], "gate")

    def test_real_red_failing_a_product_test_is_product(self) -> None:
        raw = [
            _pr(
                11,
                [
                    {"name": "merge-gate", "status": "COMPLETED",
                     "conclusion": "FAILURE"},
                    {"name": "Regular tests (GitHub-hosted)", "status": "COMPLETED",
                     "conclusion": "FAILURE"},
                ],
            ),
        ]
        status = pr_status._classify_gh_prs("rrnewton/reverie", raw)
        self.assertEqual(status.real_reds, 1)
        self.assertEqual(status.product_reds, 1)
        self.assertEqual(status.gate_reds, 0)
        self.assertEqual(status.prs[0]["real_red_kind"], "product")

    def test_pin_freshness_red_with_merge_gate_is_gate_not_product(self) -> None:
        """THE #1711 FIXTURE, measured 2026-08-07.

        Head 2d4866a0 failed exactly `reverie-pin-is-latest-main` +
        `merge-gate-v4` and was reported as real_reds=1 (product=1). It was a
        shared pin drift: the PR pinned reverie dd3c178e, IDENTICAL to hermit
        main, while reverie main had moved to 0ae0c01b. One unrecognised check
        name defeated the all-gate test and manufactured a phantom product break
        that would have sent an agent to debug the PR's code.
        """
        raw = [
            _pr(
                1711,
                [
                    {"name": "reverie-pin-is-latest-main", "status": "COMPLETED",
                     "conclusion": "FAILURE"},
                    {"name": "merge-gate-v4", "status": "COMPLETED",
                     "conclusion": "FAILURE"},
                ],
            ),
        ]
        status = pr_status._classify_gh_prs("rrnewton/hermit", raw)
        self.assertEqual(status.real_reds, 1, "still a real red -- it blocks landing")
        self.assertEqual(status.gate_reds, 1)
        self.assertEqual(status.product_reds, 0, "pin drift is not a product break")
        self.assertEqual(status.prs[0]["real_red_kind"], "gate")

    def test_pin_freshness_red_alone_is_gate(self) -> None:
        for name in (
            "reverie-pin-is-latest-main",
            "liteinst2-pin-is-latest-main",
            "Pin Is Latest Main",
        ):
            with self.subTest(check=name):
                raw = [_pr(1712, [{"name": name, "status": "COMPLETED",
                                   "conclusion": "FAILURE"}])]
                status = pr_status._classify_gh_prs("rrnewton/hermit", raw)
                self.assertEqual(status.gate_reds, 1)
                self.assertEqual(status.product_reds, 0)

    def test_pin_gate_does_not_swallow_a_real_product_failure(self) -> None:
        """The fix must not become a way to hide breakage: a pin red ALONGSIDE a
        genuine product test failure is still product."""
        raw = [
            _pr(
                1713,
                [
                    {"name": "reverie-pin-is-latest-main", "status": "COMPLETED",
                     "conclusion": "FAILURE"},
                    {"name": "Regular tests (GitHub-hosted)", "status": "COMPLETED",
                     "conclusion": "FAILURE"},
                ],
            ),
        ]
        status = pr_status._classify_gh_prs("rrnewton/hermit", raw)
        self.assertEqual(status.product_reds, 1)
        self.assertEqual(status.gate_reds, 0)
        self.assertEqual(status.prs[0]["real_red_kind"], "product")

    def test_absence_of_evidence_is_never_product(self) -> None:
        """A red whose rollup names NO failing check, with no exact-head ledger
        failure, cannot be product: nothing identifies a break, so calling it
        product would send an agent to debug code on no evidence. It stays a
        real red so the split never hides it."""
        raw = [_pr(1714, [{"name": "", "status": "COMPLETED",
                           "conclusion": "FAILURE"}])]
        status = pr_status._classify_gh_prs("rrnewton/hermit", raw)
        self.assertEqual(status.real_reds, 1, "must not be hidden")
        self.assertEqual(status.product_reds, 0, "no evidence => never product")
        self.assertEqual(status.gate_reds, 1)

    def test_core_review_protocol_only_red_is_gate(self) -> None:
        raw = [
            _pr(
                12,
                [
                    {"name": "core-review-protocol", "status": "COMPLETED",
                     "conclusion": "FAILURE"},
                ],
            ),
        ]
        status = pr_status._classify_gh_prs("rrnewton/hermit", raw)
        self.assertEqual(status.gate_reds, 1)
        self.assertEqual(status.product_reds, 0)

    def test_latest_same_head_duplicate_controls_health(self) -> None:
        sha = "a" * 40
        older = {
            "name": "merge-gate",
            "headSha": sha,
            "status": "COMPLETED",
            "conclusion": "FAILURE",
            "startedAt": "2026-08-04T15:12:05Z",
            "detailsUrl": "https://github.com/o/r/actions/runs/10/job/1",
        }
        newer = {
            "name": "merge-gate",
            "headSha": sha,
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "startedAt": "2026-08-04T15:24:36Z",
            "detailsUrl": "https://github.com/o/r/actions/runs/11/job/2",
        }
        for rollup in ([older, newer], [newer, older]):
            with self.subTest(order=[item["conclusion"] for item in rollup]):
                self.assertEqual(
                    pr_status._rollup_ci_state(rollup, head_sha=sha), "green"
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


class SetupOnlyRollupClassificationTests(unittest.TestCase):
    HEAD = "d282a85726a5e0101cad069c2f3d6e2e23b9d6cd"

    @staticmethod
    def failed_check(
        *,
        name: str = "reverie-pin-is-latest-main",
        run: int = 31114544049,
        job: int = 92660569815,
    ) -> dict[str, object]:
        return {
            "__typename": "CheckRun",
            "completedAt": "2026-08-06T15:46:01Z",
            "conclusion": "FAILURE",
            "detailsUrl": (
                f"https://github.com/rrnewton/hermit/actions/runs/{run}/job/{job}"
            ),
            "name": name,
            "startedAt": "2026-08-06T15:40:35Z",
            "status": "COMPLETED",
            "workflowName": "Merge Gate",
        }

    @staticmethod
    def accepted(
        _repo: str, _check: dict[str, object], _head: str
    ) -> pr_status.SetupOnlyVerification:
        return pr_status.SetupOnlyVerification(
            True, "setup only", run_id=31114544049, job_id=92660569815
        )

    @staticmethod
    def refused(
        _repo: str, _check: dict[str, object], _head: str
    ) -> pr_status.SetupOnlyVerification:
        return pr_status.SetupOnlyVerification(False, "identity mismatch")

    def test_1665_setup_only_failure_becomes_visible_no_result(self) -> None:
        rollup = [
            self.failed_check(),
            {
                "__typename": "CheckRun",
                "name": "merge-gate-v4",
                "status": "QUEUED",
                "conclusion": "",
                "detailsUrl": (
                    "https://github.com/rrnewton/hermit/actions/runs/31114544049/"
                    "job/92670128104"
                ),
            },
        ]
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit",
            [_pr(1665, rollup, head=self.HEAD)],
            setup_only_verifier=self.accepted,
        )
        self.assertEqual(status.pending, 1)
        self.assertEqual(status.red, 0)
        self.assertEqual(status.real_reds, 0)
        self.assertEqual(status.setup_only_no_result_checks, 1)
        self.assertEqual(
            status.prs[0]["setup_only_no_result_checks"],
            ("reverie-pin-is-latest-main",),
        )
        self.assertEqual(status.prs[0]["failing_checks"], ())

    def test_setup_only_failure_blocks_green_even_when_everything_else_passes(
        self,
    ) -> None:
        result = pr_status._classify_rollup(
            "rrnewton/hermit",
            [
                self.failed_check(),
                {
                    "__typename": "CheckRun",
                    "name": "hosted tests",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                },
            ],
            head_sha=self.HEAD,
            setup_only_verifier=self.accepted,
        )
        self.assertEqual(result.state, "pending")
        self.assertEqual(
            result.setup_only_no_result_checks,
            ("reverie-pin-is-latest-main",),
        )

    def test_1697_product_failure_stays_real_red(self) -> None:
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit",
            [
                _pr(
                    1697,
                    [
                        self.failed_check(
                            name="P0 demo gate (demos 1-8)",
                            run=31110129926,
                            job=92645431859,
                        )
                    ],
                    head="d93d512826c522dff89a27a1aa2d4eda0377796b",
                )
            ],
            setup_only_verifier=self.refused,
        )
        self.assertEqual(status.pending, 0)
        self.assertEqual(status.red, 1)
        self.assertEqual(status.real_reds, 1)
        self.assertEqual(status.product_reds, 1)
        self.assertEqual(status.setup_only_no_result_checks, 0)
        self.assertEqual(status.prs[0]["failing_checks"], ("P0 demo gate (demos 1-8)",))

    def test_mixed_setup_only_and_product_failure_remains_red(self) -> None:
        def verifier(
            _repo: str, check: dict[str, object], _head: str
        ) -> pr_status.SetupOnlyVerification:
            if check.get("name") == "setup-infra":
                return pr_status.SetupOnlyVerification(
                    True, "setup only", run_id=1, job_id=2
                )
            return pr_status.SetupOnlyVerification(False, "product steps ran")

        result = pr_status._classify_rollup(
            "rrnewton/hermit",
            [
                self.failed_check(name="setup-infra", job=92660569815),
                self.failed_check(name="product", job=92645431859),
            ],
            head_sha=self.HEAD,
            setup_only_verifier=verifier,
        )
        self.assertEqual(result.state, "red")
        self.assertEqual(result.setup_only_no_result_checks, ("setup-infra",))
        self.assertEqual(result.failing_check_names, ("product",))

    def test_batch_authority_preserves_source_and_downstream_no_result(self) -> None:
        class BatchAuthority:
            def __call__(self, *_args: object) -> pr_status.SetupOnlyVerification:
                raise AssertionError("rollup must use the single batch authority")

            def verify_failures(
                self,
                _repo: str,
                checks: tuple[dict[str, object], ...],
                _head: str,
            ) -> tuple[pr_status.SetupOnlyVerification, ...]:
                results = []
                for check in checks:
                    if check.get("name") == "reverie-pin-is-latest-main":
                        results.append(
                            pr_status.SetupOnlyVerification(
                                True,
                                "setup only",
                                31114544049,
                                92660569815,
                                kind="setup-only",
                            )
                        )
                    else:
                        results.append(
                            pr_status.SetupOnlyVerification(
                                True,
                                "prerequisite consequence",
                                31114544049,
                                92670128104,
                                kind="prerequisite-no-result",
                                source_job_id=92660569815,
                            )
                        )
                return tuple(results)

        downstream = self.failed_check(name="merge-gate-v4", job=92670128104)
        downstream["startedAt"] = "2026-08-06T18:20:19Z"
        downstream["completedAt"] = "2026-08-06T18:20:25Z"
        result = pr_status._classify_rollup(
            "rrnewton/hermit",
            [downstream, self.failed_check()],
            head_sha=self.HEAD,
            setup_only_verifier=BatchAuthority(),
        )
        self.assertEqual(result.state, "pending")
        self.assertEqual(
            result.setup_only_no_result_checks,
            ("reverie-pin-is-latest-main",),
        )
        self.assertEqual(result.prerequisite_no_result_checks, ("merge-gate-v4",))
        self.assertEqual(result.failing_check_names, ())
        self.assertIn("source_job=92660569815", result.prerequisite_evidence[0])

    def test_latest_attempt_is_selected_once_for_state_and_names(self) -> None:
        old_product = self.failed_check(name="same", run=10, job=100)
        new_setup = self.failed_check(name="same", run=11, job=101)
        seen: list[str] = []

        def verifier(
            _repo: str, check: dict[str, object], _head: str
        ) -> pr_status.SetupOnlyVerification:
            seen.append(str(check["detailsUrl"]))
            return pr_status.SetupOnlyVerification(True, "setup only", 11, 101)

        for rollup in ([old_product, new_setup], [new_setup, old_product]):
            with self.subTest(order=[c["detailsUrl"] for c in rollup]):
                seen.clear()
                result = pr_status._classify_rollup(
                    "rrnewton/hermit",
                    rollup,
                    head_sha=self.HEAD,
                    setup_only_verifier=verifier,
                )
                self.assertEqual(result.state, "pending")
                self.assertEqual(result.failing_check_names, ())
                self.assertEqual(len(seen), 1)
                self.assertIn("/runs/11/job/101", seen[0])

    def test_verifier_exception_is_fail_closed_and_visible(self) -> None:
        def broken(
            _repo: str, _check: dict[str, object], _head: str
        ) -> pr_status.SetupOnlyVerification:
            raise RuntimeError("boom")

        result = pr_status._classify_rollup(
            "rrnewton/hermit",
            [self.failed_check()],
            head_sha=self.HEAD,
            setup_only_verifier=broken,
        )
        self.assertEqual(result.state, "red")
        self.assertEqual(result.failing_check_names, ("reverie-pin-is-latest-main",))
        self.assertIn("verifier error", result.actions_job_verification_errors[0])

    def test_nonfailure_no_result_never_invokes_setup_verifier(self) -> None:
        for conclusion in ("CANCELLED", "STALE", "FUTURE"):
            with self.subTest(conclusion=conclusion):
                check = self.failed_check()
                check["conclusion"] = conclusion
                verifier = mock.Mock()
                result = pr_status._classify_rollup(
                    "rrnewton/hermit",
                    [check],
                    head_sha=self.HEAD,
                    setup_only_verifier=verifier,
                )
                self.assertEqual(result.state, "pending")
                self.assertEqual(result.setup_only_no_result_checks, ())
                verifier.assert_not_called()


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

    @mock.patch("pr_status.banked_failure_tier_commits", return_value={})
    @mock.patch("pr_status.banked_green_commits", return_value=frozenset())
    @mock.patch("pr_status.time.sleep")
    @mock.patch("pr_status.subprocess.run")
    def test_transient_stream_error_retries(
        self, run, sleep, _green, _failure
    ) -> None:
        # Isolate the gh-fetch retry path: stub the ledger cross-references so the
        # mocked `subprocess.run` side_effect models ONLY the gh call (error then
        # success), and the test does not read the machine-local ledger.
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

    @mock.patch("pr_status.banked_failure_tier_commits", return_value={})
    @mock.patch("pr_status.banked_green_commits", return_value=frozenset())
    @mock.patch("pr_status.subprocess.run")
    def test_fetch_consumer_dereferences_setup_failure(
        self, run: mock.Mock, _green: mock.Mock, _failure: mock.Mock
    ) -> None:
        check = SetupOnlyRollupClassificationTests.failed_check()
        raw = [_pr(1665, [check], head=SetupOnlyRollupClassificationTests.HEAD)]
        fixture = (
            Path(__file__).resolve().parent / "fixtures/actions_job_92660569815.json"
        ).read_text()
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(raw), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=fixture, stderr=""
            ),
        ]
        status = pr_status.fetch_repo_status_gh(
            "rrnewton/hermit", net_wrapper=[], timeout=30
        )
        self.assertEqual(status.pending, 1)
        self.assertEqual(status.red, 0)
        self.assertEqual(status.setup_only_no_result_checks, 1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[1].args[0][-1],
            "repos/rrnewton/hermit/actions/jobs/92660569815",
        )

    @mock.patch("pr_status.banked_failure_tier_commits", return_value={})
    @mock.patch("pr_status.banked_green_commits", return_value=frozenset())
    @mock.patch("pr_status.subprocess.run")
    def test_fetch_consumer_preserves_genuine_product_red(
        self, run: mock.Mock, _green: mock.Mock, _failure: mock.Mock
    ) -> None:
        check = SetupOnlyRollupClassificationTests.failed_check(
            name="P0 demo gate (demos 1-8)",
            run=31110129926,
            job=92645431859,
        )
        raw = [_pr(1697, [check], head="d93d512826c522dff89a27a1aa2d4eda0377796b")]
        fixture = (
            Path(__file__).resolve().parent / "fixtures/actions_job_92645431859.json"
        ).read_text()
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(raw), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=fixture, stderr=""
            ),
        ]
        status = pr_status.fetch_repo_status_gh(
            "rrnewton/hermit", net_wrapper=[], timeout=30
        )
        self.assertEqual(status.pending, 0)
        self.assertEqual(status.red, 1)
        self.assertEqual(status.product_reds, 1)
        self.assertEqual(status.setup_only_no_result_checks, 0)

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
        self.assertIn("Verdict rule:", report)
        self.assertIn("local validation receipts", report)

    def test_unhealthy_verdict_names_exact_trigger_inputs(self) -> None:
        status = pr_status.RepoStatus(
            repo="rrnewton/hermit",
            open=4,
            green=1,
            red=3,
            pending=0,
            real_reds=2,
            outage_suspected=False,
            prs=(
                {"red_class": "real-red"},
                {"red_class": "real-red"},
                {"red_class": "stale-base"},
            ),
        )

        verdict = pr_status.health_verdict([status])
        self.assertEqual(verdict["state"], "unhealthy")
        self.assertEqual(verdict["inputs"][0]["real_reds"], 2)
        self.assertEqual(verdict["inputs"][0]["stale_base_reds"], 1)
        self.assertTrue(verdict["inputs"][0]["triggers_unhealthy"])
        report = pr_status.render_report([status], warn_threshold=10, engine="gh")
        self.assertIn("real_reds=2", report)
        self.assertIn("stale_base_reds=1", report)
        self.assertIn("triggers_unhealthy=True", report)

    def test_render_flags_gate_only_unhealthy_as_no_product_break(self) -> None:
        # All real-reds are gate reds => the note must say product-test reds=0.
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit",
            [
                _pr(
                    1,
                    [{"name": "merge-gate-v2", "status": "COMPLETED",
                      "conclusion": "FAILURE"}],
                    merge_state="BLOCKED",
                ),
            ],
        )
        report = pr_status.render_report([status], warn_threshold=10, engine="gh")
        self.assertIn("CI health: UNHEALTHY", report)
        self.assertIn("0 product-test reds", report)
        self.assertIn("(product=0 gate=1)", report)

    def test_render_points_at_product_red_when_present(self) -> None:
        status = pr_status._classify_gh_prs(
            "rrnewton/reverie",
            [
                _pr(
                    2,
                    [{"name": "Regular tests (GitHub-hosted)", "status": "COMPLETED",
                      "conclusion": "FAILURE"}],
                    merge_state="BLOCKED",
                ),
            ],
        )
        report = pr_status.render_report([status], warn_threshold=10, engine="gh")
        self.assertIn("CI health: UNHEALTHY", report)
        self.assertIn("1 product-test red(s)", report)
        self.assertIn("rrnewton/reverie=1", report)

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


class ExecutedTestsCarveOutTests(unittest.TestCase):
    """The PR-facing peer of the ledger-side named-gate predicate: a GitHub
    product red whose EXACT head carries no named failing gate locally is a
    NO-RESULT, not a real red. Bind the demotion to the observed named-gate
    authority (gates[]), never a check or executed-test count (Proxy Binding)."""

    # The canonical same-check-count control pair (both rows report SIX gates;
    # only the named-gate evidence separates them). 40-hex heads padded from the
    # real ledger commit prefixes so the ledger row and the PR head bind by exact SHA.
    HEAD_GENUINE = "17b59fc6" + "1" * 32  # named failing gate -> stays RED (ok)
    HEAD_NO_RESULT = "98573d14" + "2" * 32  # no named gate, no count -> NO-RESULT
    HEAD_NEEDS_RERUN = "54b4d4e5" + "3" * 32  # failure count, no named gate -> NEEDS-RERUN

    def _product_fail(self, number: int, head: str):
        # A genuine PRODUCT-test failing check (not a gate meta-check), base known
        # and clean, so classification reaches the real-product-red branch where
        # the executed_tests carve-out applies.
        return _pr(
            number,
            [
                {
                    "name": "Regular tests (GitHub-hosted)",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                }
            ],
            merge_state="CLEAN",
            head=head,
        )

    def test_tier_derives_from_named_gate_not_check_count(self) -> None:
        # banked_failure_tier_commits reads the machine-local ledger and tiers each
        # fail row by the NAMED-GATE predicate. All control rows report six gates
        # (gates_run=6); only the named-gate evidence separates their tiers.
        import tempfile

        genuine_gate = [{"name": "portable CI DAG lane", "result": "fail",
                         "exit_code": 101, "real_seconds": 221}]
        ledger = "\n".join(
            json.dumps(row)
            for row in [
                {"result": "fail", "commit": self.HEAD_GENUINE,
                 "gates_run": 6, "gates_expected": 6, "failures": 1,
                 "gates": genuine_gate, "cwd": "/w/hermit"},
                {"result": "fail", "commit": self.HEAD_NO_RESULT,
                 "gates_run": 6, "gates_expected": 6, "gates": [],
                 "cwd": "/w/hermit"},
                {"result": "timeout", "commit": self.HEAD_NEEDS_RERUN,
                 "gates_run": 6, "gates_expected": 6, "failures": 1, "gates": [],
                 "cwd": "/w/hermit"},
                # strongest-wins: a second run at the genuine head with no named gate
                # must NOT downgrade the real named-gate failure.
                {"result": "fail", "commit": self.HEAD_GENUINE,
                 "gates_run": 6, "gates_expected": 6, "gates": [],
                 "cwd": "/w/hermit"},
                # repo isolation: a reverie fail must not appear for hermit.
                {"result": "fail", "commit": "b" * 40,
                 "gates": [], "cwd": "/w/reverie"},
                # a PASS row is not a failure tier.
                {"result": "pass", "commit": "c" * 40,
                 "gates": genuine_gate, "cwd": "/w/hermit"},
            ]
        )
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ignored").mkdir()
            (Path(d) / "ignored" / "validate-run-ledger.jsonl").write_text(ledger)
            with mock.patch.object(pr_status, "ROOT", Path(d)):
                tiers = pr_status.banked_failure_tier_commits("rrnewton/hermit")
        self.assertEqual(tiers[self.HEAD_GENUINE], "ok")  # named gate wins over the no-gate dup
        self.assertEqual(tiers[self.HEAD_NO_RESULT], "no-result")
        self.assertEqual(tiers[self.HEAD_NEEDS_RERUN], "needs-rerun")
        self.assertNotIn("b" * 40, tiers)  # reverie isolated
        self.assertNotIn("c" * 40, tiers)  # pass is not a failure

    def test_no_result_red_demoted_but_genuine_red_stays(self) -> None:
        # PLANT BOTH WAYS in one call: the tests=1 head demotes to NO-RESULT while
        # the tests=760 full-failure head STILL classifies as a real product red.
        raw = [
            self._product_fail(1470, self.HEAD_GENUINE),
            self._product_fail(1443, self.HEAD_NO_RESULT),
            self._product_fail(9999, self.HEAD_NEEDS_RERUN),
        ]
        banked_failure = {
            self.HEAD_GENUINE: "ok",
            self.HEAD_NO_RESULT: "no-result",
            self.HEAD_NEEDS_RERUN: "needs-rerun",
        }
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit", raw, banked_failure=banked_failure
        )
        # Only the genuine full-suite failure is a real red; the other two demote.
        self.assertEqual(status.real_reds, 1)
        self.assertEqual(status.product_reds, 1)
        self.assertEqual(status.ledger_no_result, 1)
        self.assertEqual(status.ledger_needs_rerun, 1)
        by_pr = {pr["pr"]: pr for pr in status.prs}
        self.assertEqual(by_pr[1470]["red_class"], "real-red")
        self.assertEqual(by_pr[1470]["real_red_kind"], "product")
        self.assertEqual(by_pr[1443]["red_class"], "ledger-no-result")
        self.assertEqual(by_pr[9999]["red_class"], "ledger-needs-rerun")

    def test_no_local_row_keeps_github_verdict(self) -> None:
        # A red head with NO local ledger row is untouched — a hosted-only red keeps
        # the GitHub verdict (the ledger is machine-local; most heads have no row).
        raw = [self._product_fail(1200, "d" * 40)]
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit", raw, banked_failure={}
        )
        self.assertEqual(status.real_reds, 1)
        self.assertEqual(status.ledger_no_result, 0)

    def test_gate_red_not_demoted_by_ledger_tier(self) -> None:
        # A landing-gate/review meta-check red is a genuine blocker regardless of the
        # local test count (a DIFFERENT authority) — it must NOT be demoted even if a
        # no-result ledger row exists at the head.
        raw = [
            _pr(
                1147,
                [{"name": "merge-gate-v4", "status": "COMPLETED",
                  "conclusion": "FAILURE"}],
                merge_state="BLOCKED",
                head=self.HEAD_NO_RESULT,
            )
        ]
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit",
            raw,
            banked_failure={self.HEAD_NO_RESULT: "no-result"},
        )
        self.assertEqual(status.real_reds, 1)
        self.assertEqual(status.gate_reds, 1)
        self.assertEqual(status.ledger_no_result, 0)

    def test_demotion_is_observable_in_render_and_verdict(self) -> None:
        # The demotion must not be silent: the health verdict and report both carry
        # the counts, so a red missing from real_reds is explained, not vanished.
        raw = [
            self._product_fail(1443, self.HEAD_NO_RESULT),
            self._product_fail(9999, self.HEAD_NEEDS_RERUN),
        ]
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit",
            raw,
            banked_failure={
                self.HEAD_NO_RESULT: "no-result",
                self.HEAD_NEEDS_RERUN: "needs-rerun",
            },
        )
        verdict = pr_status.health_verdict([status])
        self.assertEqual(verdict["state"], "healthy")  # both demoted -> not unhealthy
        self.assertEqual(verdict["inputs"][0]["ledger_no_result"], 1)
        self.assertEqual(verdict["inputs"][0]["ledger_needs_rerun"], 1)
        report = pr_status.render_report([status], warn_threshold=10, engine="gh")
        self.assertIn("ledger_no_result=1", report)
        self.assertIn("ledger_needs_rerun=1", report)

    def test_ledger_green_beats_failure_tier(self) -> None:
        # If the exact head has BOTH a green receipt and a fail row, the green
        # receipt (a complete PASS) wins: green_local, not a demoted no-result.
        raw = [self._product_fail(1624, self.HEAD_NO_RESULT)]
        status = pr_status._classify_gh_prs(
            "rrnewton/hermit",
            raw,
            banked_green=frozenset({self.HEAD_NO_RESULT}),
            banked_failure={self.HEAD_NO_RESULT: "no-result"},
        )
        self.assertEqual(status.green_local, 1)
        self.assertEqual(status.ledger_no_result, 0)
        self.assertEqual(status.real_reds, 0)



class ReviewEvidenceBindingTests(unittest.TestCase):
    """A `passed-review-*` label must not authorize a head it was not earned on.

    Live instance, rrnewton/reverie#394: PASS earned at
    92e1e0d0af65e50cd2991d4deaa25f726832fbf4, head rebased to
    0fc9f61edc01d6425def2efb0ed82f01410c7fcc, `passed-review-claude` still
    applied. The label's GitHub description says "PASSED at current PR head",
    so the label asserted a binding that had become false.
    """

    EARNED = "92e1e0d0af65e50cd2991d4deaa25f726832fbf4"
    HEAD_AFTER_REBASE = "0fc9f61edc01d6425def2efb0ed82f01410c7fcc"

    # ---- NEGATIVE: the live stale case must not read as approval ----

    def test_the_live_pr394_rebase_reads_as_stale_not_approved(self):
        state = pr_status.approval_binding(self.EARNED, self.HEAD_AFTER_REBASE)
        self.assertEqual(state, pr_status.APPROVAL_STALE)
        self.assertNotEqual(state, pr_status.APPROVAL_BOUND)

    def test_stale_is_distinct_from_never_reviewed(self):
        # Collapsing these loses both the warning and the reviewer's work.
        self.assertNotEqual(
            pr_status.approval_binding(self.EARNED, self.HEAD_AFTER_REBASE),
            pr_status.approval_binding(None, self.HEAD_AFTER_REBASE),
        )

    def test_every_ambiguous_input_fails_closed(self):
        for pass_sha, head in (
            (None, self.HEAD_AFTER_REBASE),      # no PASS recorded
            (self.EARNED, None),                 # head unknown
            ("92e1e0d0", self.HEAD_AFTER_REBASE),  # abbreviated PASS sha
            (self.EARNED, "0fc9f61e"),           # abbreviated head
            ("", ""),
        ):
            self.assertEqual(
                pr_status.approval_binding(pass_sha, head),
                pr_status.APPROVAL_UNBOUND,
                f"{pass_sha!r} vs {head!r} must not authorize",
            )

    # ---- POSITIVE: a PASS on the current head still authorizes ----

    def test_a_pass_on_the_current_head_binds(self):
        self.assertEqual(
            pr_status.approval_binding(self.EARNED, self.EARNED),
            pr_status.APPROVAL_BOUND,
        )
        # Case-insensitive and whitespace-tolerant, so a copied SHA still binds.
        self.assertEqual(
            pr_status.approval_binding(self.EARNED.upper(), f" {self.EARNED} "),
            pr_status.APPROVAL_BOUND,
        )

    # ---- the SHA is read from the reviewer's own words ----

    def test_the_pass_sha_is_extracted_from_real_verdict_shapes(self):
        for body in (
            f"**VERDICT: PASS at `{self.EARNED}`.**",
            f"[orc-coord-014] **VERDICT: PASS** at `{self.EARNED}`.",
            f"## `[orc-coord-014]` PASS - independent re-review at head `{self.EARNED}`",
        ):
            self.assertEqual(pr_status.extract_pass_sha(body), self.EARNED, body[:60])

    def test_a_block_verdict_and_an_unanchored_pass_yield_no_sha(self):
        # A BLOCK must never be mined for a SHA, and a PASS naming no commit
        # cannot bind to anything.
        self.assertIsNone(
            pr_status.extract_pass_sha(f"**VERDICT: BLOCK at `{self.EARNED}`.**")
        )
        self.assertIsNone(pr_status.extract_pass_sha("VERDICT: PASS. Looks good."))
        self.assertIsNone(pr_status.extract_pass_sha(""))

if __name__ == "__main__":
    unittest.main()

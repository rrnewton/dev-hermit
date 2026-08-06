#!/usr/bin/env python3
"""Brackets for the exact GitHub Actions setup-only authority."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HEALTH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HEALTH))

import actions_job_outcome as outcome  # noqa: E402


FIXTURES = Path(__file__).resolve().parent / "fixtures"
POSITIVE_HEAD = "d282a85726a5e0101cad069c2f3d6e2e23b9d6cd"
NEGATIVE_HEAD = "d93d512826c522dff89a27a1aa2d4eda0377796b"


def _fixture(job_id: int) -> dict[str, object]:
    return json.loads((FIXTURES / f"actions_job_{job_id}.json").read_text())


def _positive_check() -> dict[str, object]:
    return {
        "__typename": "CheckRun",
        "completedAt": "2026-08-06T15:46:01Z",
        "conclusion": "FAILURE",
        "detailsUrl": (
            "https://github.com/rrnewton/hermit/actions/runs/31114544049/"
            "job/92660569815"
        ),
        "name": "reverie-pin-is-latest-main",
        "startedAt": "2026-08-06T15:40:35Z",
        "status": "COMPLETED",
        "workflowName": "Merge Gate",
    }


def _negative_check() -> dict[str, object]:
    return {
        "__typename": "CheckRun",
        "completedAt": "2026-08-06T15:12:56Z",
        "conclusion": "FAILURE",
        "detailsUrl": (
            "https://github.com/rrnewton/hermit/actions/runs/31110129926/"
            "job/92645431859"
        ),
        "name": "P0 demo gate (demos 1-8)",
        "startedAt": "2026-08-06T15:07:52Z",
        "status": "COMPLETED",
        "workflowName": "P0 Demo Gate (Hermit hot paths)",
    }


class PayloadVerifierTests(unittest.TestCase):
    def verify(
        self,
        check: dict[str, object],
        head: str,
        payload: object,
    ) -> outcome.SetupOnlyVerification:
        return outcome.verify_setup_only_job_payload(
            repo="rrnewton/hermit", check=check, head_sha=head, payload=payload
        )

    def test_live_positive_1665_setup_only_job_is_accepted(self) -> None:
        result = self.verify(_positive_check(), POSITIVE_HEAD, _fixture(92660569815))
        self.assertTrue(result.accepted)
        self.assertEqual(result.run_id, 31114544049)
        self.assertEqual(result.job_id, 92660569815)

    def test_live_negative_1697_product_job_is_refused(self) -> None:
        result = self.verify(_negative_check(), NEGATIVE_HEAD, _fixture(92645431859))
        self.assertFalse(result.accepted)
        self.assertIn("exactly one step, observed 18", result.reason)

    def test_identity_name_url_head_and_timestamps_are_all_bound(self) -> None:
        payload = _fixture(92660569815)
        mutations = {
            "job-id": ("id", 92660569816),
            "run-id": ("run_id", 31114544050),
            "head": ("head_sha", "f" * 40),
            "api-url": (
                "url",
                "https://api.github.com/repos/other/repo/actions/jobs/1",
            ),
            "html-url": (
                "html_url",
                "https://github.com/other/repo/actions/runs/1/job/1",
            ),
            "run-url": (
                "run_url",
                "https://api.github.com/repos/other/repo/actions/runs/1",
            ),
            "check-url": (
                "check_run_url",
                "https://api.github.com/repos/other/repo/check-runs/1",
            ),
            "name": ("name", "different"),
            "workflow": ("workflow_name", "different"),
            "started": ("started_at", "2026-08-06T15:40:34Z"),
            "completed": ("completed_at", "2026-08-06T15:46:02Z"),
            "status": ("status", "queued"),
            "conclusion": ("conclusion", "cancelled"),
        }
        for label, (field, value) in mutations.items():
            with self.subTest(label=label):
                changed = copy.deepcopy(payload)
                changed[field] = value
                self.assertFalse(
                    self.verify(_positive_check(), POSITIVE_HEAD, changed).accepted
                )

    def test_tampered_rollup_identity_is_refused(self) -> None:
        changes = {
            "repo": "https://github.com/rrnewton/reverie/actions/runs/31114544049/job/92660569815",
            "run": "https://github.com/rrnewton/hermit/actions/runs/1/job/92660569815",
            "job": "https://github.com/rrnewton/hermit/actions/runs/31114544049/job/1",
            "query": "https://github.com/rrnewton/hermit/actions/runs/31114544049/job/92660569815?x=1",
        }
        for label, url in changes.items():
            with self.subTest(label=label):
                check = _positive_check()
                check["detailsUrl"] = url
                self.assertFalse(
                    self.verify(check, POSITIVE_HEAD, _fixture(92660569815)).accepted
                )

    def test_multiple_failure_or_any_later_step_is_refused(self) -> None:
        for conclusion in ("failure", "success", "skipped"):
            with self.subTest(conclusion=conclusion):
                payload = _fixture(92660569815)
                steps = payload["steps"]
                assert isinstance(steps, list)
                steps.append(
                    {
                        "name": "Run product tests",
                        "number": 2,
                        "status": "completed",
                        "conclusion": conclusion,
                        "started_at": "2026-08-06T15:45:36Z",
                        "completed_at": "2026-08-06T15:45:37Z",
                    }
                )
                result = self.verify(_positive_check(), POSITIVE_HEAD, payload)
                self.assertFalse(result.accepted)
                self.assertIn("exactly one step", result.reason)

    def test_paginated_malformed_or_missing_payload_is_refused(self) -> None:
        for payload in (None, [], {}, {"total_count": 1, "jobs": []}):
            with self.subTest(payload=payload):
                self.assertFalse(
                    self.verify(_positive_check(), POSITIVE_HEAD, payload).accepted
                )

    def test_cancelled_stale_and_unknown_rollup_outcomes_never_accept(self) -> None:
        for conclusion in ("CANCELLED", "STALE", "FUTURE"):
            with self.subTest(conclusion=conclusion):
                check = _positive_check()
                check["conclusion"] = conclusion
                self.assertFalse(
                    self.verify(check, POSITIVE_HEAD, _fixture(92660569815)).accepted
                )


class BoundedDereferenceTests(unittest.TestCase):
    def authority(self, **kwargs: object) -> outcome.GitHubActionsJobAuthority:
        return outcome.GitHubActionsJobAuthority(
            "rrnewton/hermit",
            net_wrapper=["with-proxy"],
            deadline=time.monotonic() + 30,
            **kwargs,
        )

    @mock.patch("actions_job_outcome.subprocess.run")
    def test_constructs_exact_job_endpoint_and_caches(self, run: mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(_fixture(92660569815)),
            stderr="",
        )
        authority = self.authority()
        first = authority("rrnewton/hermit", _positive_check(), POSITIVE_HEAD)
        second = authority("rrnewton/hermit", _positive_check(), POSITIVE_HEAD)
        self.assertTrue(first.accepted)
        self.assertTrue(second.accepted)
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(
            command,
            [
                "with-proxy",
                "gh",
                "api",
                "--method",
                "GET",
                "repos/rrnewton/hermit/actions/jobs/92660569815",
            ],
        )

    @mock.patch("actions_job_outcome.subprocess.run")
    def test_well_shaped_nonexistent_job_retains_failure(self, run: mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="HTTP 404: Not Found"
        )
        result = self.authority()("rrnewton/hermit", _positive_check(), POSITIVE_HEAD)
        self.assertFalse(result.accepted)
        self.assertIn("404", result.reason)

    @mock.patch("actions_job_outcome.subprocess.run")
    def test_verifier_process_error_retains_failure(self, run: mock.Mock) -> None:
        run.side_effect = OSError("cannot execute")
        result = self.authority()("rrnewton/hermit", _positive_check(), POSITIVE_HEAD)
        self.assertFalse(result.accepted)
        self.assertIn("could not start", result.reason)

    @mock.patch("actions_job_outcome.subprocess.run")
    def test_verifier_timeout_retains_failure(self, run: mock.Mock) -> None:
        run.side_effect = subprocess.TimeoutExpired(cmd="gh api", timeout=1)
        result = self.authority()("rrnewton/hermit", _positive_check(), POSITIVE_HEAD)
        self.assertFalse(result.accepted)
        self.assertIn("API exceeded", result.reason)

    @mock.patch("actions_job_outcome.subprocess.run")
    def test_dereference_budget_exhaustion_does_not_call_api(
        self, run: mock.Mock
    ) -> None:
        result = self.authority(max_dereferences=0)(
            "rrnewton/hermit", _positive_check(), POSITIVE_HEAD
        )
        self.assertFalse(result.accepted)
        self.assertIn("budget exhausted", result.reason)
        run.assert_not_called()

    @mock.patch("actions_job_outcome.subprocess.run")
    def test_deadline_exhaustion_does_not_call_api(self, run: mock.Mock) -> None:
        authority = outcome.GitHubActionsJobAuthority(
            "rrnewton/hermit",
            net_wrapper=[],
            deadline=time.monotonic() - 1,
        )
        result = authority("rrnewton/hermit", _positive_check(), POSITIVE_HEAD)
        self.assertFalse(result.accepted)
        self.assertIn("deadline exhausted", result.reason)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

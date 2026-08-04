#!/usr/bin/env python3
"""Cross-consumer contract tests for PASSED / FAILED / NO_RESULT."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

CI_HUB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI_HUB))

from check_outcome import CheckOutcome, classify_check


class CheckOutcomeContractTests(unittest.TestCase):
    def test_legitimate_passes_remain_passed_n2(self) -> None:
        # N=2 positive controls: CheckRun and legacy StatusContext shapes.
        cases = (("completed", "success"), ("", "SUCCESS"))
        self.assertEqual(len(cases), 2)
        for status, conclusion in cases:
            with self.subTest(status=status, conclusion=conclusion):
                self.assertIs(
                    classify_check(status, conclusion), CheckOutcome.PASSED
                )

    def test_genuine_failures_remain_failed_n4(self) -> None:
        cases = ("failure", "timed_out", "error", "startup_failure")
        self.assertEqual(len(cases), 4)
        for conclusion in cases:
            with self.subTest(conclusion=conclusion):
                self.assertIs(
                    classify_check("completed", conclusion), CheckOutcome.FAILED
                )

    def test_no_result_is_neither_passed_nor_failed_n11(self) -> None:
        cases = (
            ("completed", "cancelled"),
            ("completed", "skipped"),
            ("completed", "neutral"),
            ("completed", "action_required"),
            ("completed", "stale"),
            ("completed", ""),
            ("queued", ""),
            ("in_progress", ""),
            ("waiting", ""),
            ("", "brand_new_github_state"),
            ("", ""),
        )
        self.assertEqual(len(cases), 11)
        for status, conclusion in cases:
            with self.subTest(status=status, conclusion=conclusion):
                outcome = classify_check(status, conclusion)
                self.assertIs(outcome, CheckOutcome.NO_RESULT)
                self.assertIsNot(outcome, CheckOutcome.PASSED)
                self.assertIsNot(outcome, CheckOutcome.FAILED)

    def test_proven_self_timeout_is_failed(self) -> None:
        self.assertIs(
            classify_check("completed", "cancelled", self_timeout=True),
            CheckOutcome.FAILED,
        )


if __name__ == "__main__":
    unittest.main()

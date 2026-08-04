#!/usr/bin/env python3
"""Completeness brackets for flake/contention classification."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flake_class as fc


class CompletionTest(unittest.TestCase):
    def test_truncated_never_becomes_flaky_or_defect(self):
        truncated = {
            "profile": "full", "result": "fail", "checks": 2,
            "gates_run": 2, "gates_expected": 5, "failures": 2,
            "gates": [{"name": "portable CI DAG lane", "result": "fail"}],
        }
        analysis = fc.classify(truncated, [truncated], {})
        self.assertEqual(analysis.verdict, "truncated")
        self.assertEqual(fc.effective_result(truncated), "truncated")

    def test_genuine_five_of_five_red_is_classified(self):
        genuine = {
            "profile": "full", "result": "fail", "checks": 5,
            "gates_run": 5, "gates_expected": 5, "failures": 1,
        }
        analysis = fc.classify(genuine, [genuine], {})
        self.assertEqual(analysis.verdict, "defect")
        self.assertEqual(fc.effective_result(genuine), "fail")

    def test_old_reconstructed_four_gate_red_is_not_rewritten(self):
        old_complete = {
            "schema_version": 1, "profile": "full", "result": "fail",
            "checks": 4, "failures": 1, "_source": "reconstructed",
        }
        self.assertFalse(fc.is_truncated(old_complete))

    def test_two_legitimate_passes_are_unchanged(self):
        passes = [
            {"profile": "full", "result": "pass", "checks": 5,
             "gates_run": 5, "gates_expected": 5, "failures": 0},
            {"profile": "full", "result": "pass", "checks": 5,
             "gates_run": 5, "gates_expected": 5, "failures": 0},
        ]
        self.assertEqual(
            [fc.effective_result(row) for row in passes], ["pass", "pass"]
        )
        self.assertEqual(
            [fc.classify(row, passes, {}).verdict for row in passes],
            ["n/a", "n/a"],
        )


if __name__ == "__main__":
    unittest.main()

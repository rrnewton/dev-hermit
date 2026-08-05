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

    def test_command_not_found_storm_is_no_result_not_defect(self):
        # LIVE a1493427 (reverie): five failing gates at exit 127, 1s wall; the
        # same commit passed 6/6 at 58s. An env fault exercised nothing about the
        # product, so it is a no-result, never a defect.
        storm = {
            "schema_version": 3, "profile": "full", "result": "fail",
            "checks": 6, "failures": 5, "real_seconds": 1,
            "gates": [
                {"name": "Merge-gate policy", "result": "pass", "exit_code": 0, "real_seconds": 0},
                {"name": "Build workspace", "result": "fail", "exit_code": 127, "real_seconds": 0},
                {"name": "Test regular workspace cases", "result": "fail", "exit_code": 127, "real_seconds": 0},
                {"name": "Documentation tests", "result": "fail", "exit_code": 127, "real_seconds": 0},
                {"name": "Clippy", "result": "fail", "exit_code": 127, "real_seconds": 0},
                {"name": "Rustfmt", "result": "fail", "exit_code": 127, "real_seconds": 0},
            ],
        }
        self.assertTrue(fc.is_env_fault(storm))
        self.assertEqual(fc.classify(storm, [storm], {}).verdict, "no-result")
        self.assertEqual(fc.effective_result(storm), "no-result")

    def test_subsecond_collapse_all_gates_red_is_no_result(self):
        collapse = {
            "schema_version": 3, "profile": "full", "result": "fail",
            "checks": 3, "failures": 3, "real_seconds": 1,
            "gates": [
                {"name": "Build workspace", "result": "fail", "exit_code": 1, "real_seconds": 0},
                {"name": "Test regular workspace cases", "result": "fail", "exit_code": 1, "real_seconds": 0},
                {"name": "Clippy", "result": "fail", "exit_code": 1, "real_seconds": 0},
            ],
        }
        self.assertEqual(fc.classify(collapse, [collapse], {}).verdict, "no-result")

    def test_genuine_red_that_executed_is_not_laundered_as_env_fault(self):
        # A real product red: a gate failed at exit 101 after 221s. Neither
        # env-fault tell matches, so it stays a defect (the "genuine red still
        # reads FAILED" bracket).
        genuine = {
            "schema_version": 6, "profile": "full", "result": "fail",
            "checks": 5, "gates_run": 5, "gates_expected": 5, "failures": 1,
            "real_seconds": 260,
            "gates": [
                {"name": "portable CI DAG lane", "result": "fail",
                 "exit_code": 101, "real_seconds": 221},
            ],
        }
        self.assertFalse(fc.is_env_fault(genuine))
        self.assertEqual(fc.classify(genuine, [genuine], {}).verdict, "defect")
        self.assertEqual(fc.effective_result(genuine), "fail")

    def test_mixed_command_not_found_and_genuine_red_is_not_no_result(self):
        # A real defect (exit 101, 221s) co-occurring with a command-not-found
        # gate must NOT be laundered: any genuine executed red disqualifies the
        # env-fault reading.
        mixed = {
            "schema_version": 6, "profile": "full", "result": "fail",
            "checks": 5, "gates_run": 5, "gates_expected": 5, "failures": 2,
            "real_seconds": 260,
            "gates": [
                {"name": "portable CI DAG lane", "result": "fail",
                 "exit_code": 101, "real_seconds": 221},
                {"name": "Rustfmt", "result": "fail", "exit_code": 127, "real_seconds": 0},
            ],
        }
        self.assertFalse(fc.is_env_fault(mixed))
        self.assertNotEqual(fc.classify(mixed, [mixed], {}).verdict, "no-result")

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

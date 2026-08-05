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
            "executed_tests": 765,
        }
        analysis = fc.classify(genuine, [genuine], {})
        self.assertEqual(analysis.verdict, "defect")
        self.assertEqual(fc.effective_result(genuine), "fail")

    def test_low_or_absent_executed_count_red_is_no_result(self):
        # PLANT BOTH WAYS — the false-red direction, peer of the Rust
        # `low_or_absent_executed_count_red_is_no_result_not_failed` bracket. A
        # complete full-profile red (checks==6, both gate counts satisfied) is
        # STILL demoted to no-result when its own executed-test count proves it
        # exercised nothing: <=1 or an absent count. The count is the binding
        # evidence, not the check count. Live shapes: 92db28e0 / e0c96c58
        # (exec=1), 1288671f / 97eb2c75 (exec=null). Paired with
        # test_genuine_five_of_five_red_is_classified (exec=765 stays "fail"):
        # a classifier that called everything a no-result would be exactly as
        # broken as one that called everything red.
        for count in (1, 0, None):
            row = {
                "profile": "full", "result": "fail", "checks": 6,
                "gates_run": 6, "gates_expected": 6, "failures": 1,
                "executed_tests": count,
            }
            self.assertEqual(
                fc.effective_result(row), "no-result",
                f"executed_tests={count!r} full red must be a no-result",
            )
        # Control: an above-floor genuine full red is NOT laundered (both sides).
        genuine = {
            "profile": "full", "result": "fail", "checks": 6,
            "gates_run": 6, "gates_expected": 6, "failures": 1,
            "executed_tests": 765,
        }
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
            "real_seconds": 260, "executed_tests": 765,
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
            "real_seconds": 260, "executed_tests": 765,
            "gates": [
                {"name": "portable CI DAG lane", "result": "fail",
                 "exit_code": 101, "real_seconds": 221},
                {"name": "Rustfmt", "result": "fail", "exit_code": 127, "real_seconds": 0},
            ],
        }
        self.assertFalse(fc.is_env_fault(mixed))
        self.assertNotEqual(fc.classify(mixed, [mixed], {}).verdict, "no-result")

    def test_over_run_red_is_not_truncated(self):
        # OVER-RUN bracket: a run that executed MORE gates than the hardcoded
        # five-gate fallback (six live gates) is COMPLETE, not truncated. With the
        # old `ran != expected` test a 6/5 red was mislabelled "truncated" and its
        # real cause lost; only an UNDER-run (`ran < expected`) is a truncation.
        # A genuine executed red here must read "defect", not "truncated".
        over = {
            "schema_version": 6, "profile": "full", "result": "fail",
            "checks": 6, "gates_run": 6, "gates_expected": 5, "failures": 1,
            "real_seconds": 260, "executed_tests": 765,
            "gates": [
                {"name": "portable CI DAG lane", "result": "fail",
                 "exit_code": 101, "real_seconds": 221},
            ],
        }
        self.assertFalse(fc.is_truncated(over))
        self.assertEqual(fc.classify(over, [over], {}).verdict, "defect")
        self.assertEqual(fc.effective_result(over), "fail")

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

    def test_partial_profile_pass_downgrades_to_pass_partial(self):
        # A 2-check portable-strict-compat-only pass must NOT read as a full
        # green: effective_result types it `pass-partial` so a reader tells the
        # narrowed scope apart without knowing the profile taxonomy. This is the
        # live schema-4 validate.sh shape (bare result="pass", non-full profile).
        compat_only = {
            "schema_version": 4, "profile": "portable-strict-compat-only",
            "result": "pass", "checks": 2, "failures": 0, "real_seconds": 56,
        }
        self.assertEqual(fc.effective_result(compat_only), "pass-partial")
        self.assertFalse(fc.is_full_coverage(compat_only))
        # Other narrowed profiles downgrade the same way.
        for prof in ("only-portable", "portable-only", "quick"):
            row = {"profile": prof, "result": "pass", "checks": 3, "failures": 0}
            self.assertEqual(fc.effective_result(row), "pass-partial")
        # A full-profile pass is unchanged, and an explicit full_coverage=True
        # overrides a would-be-partial profile name.
        full = {"schema_version": 4, "profile": "full", "result": "pass",
                "checks": 5, "gates_run": 5, "gates_expected": 5, "failures": 0}
        self.assertEqual(fc.effective_result(full), "pass")
        self.assertTrue(fc.is_full_coverage(full))
        explicit = {"profile": "only-portable", "full_coverage": True,
                    "result": "pass", "checks": 3, "failures": 0}
        self.assertEqual(fc.effective_result(explicit), "pass")


if __name__ == "__main__":
    unittest.main()

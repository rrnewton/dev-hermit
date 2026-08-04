#!/usr/bin/env python3
"""Both-way verification for the RESULT-LEVEL zero-test-green detector.

A detector that rejects everything passes the negative test alone, so every
downgrade case is paired with a genuine-pass case that MUST still be accepted:
- NEGATIVE: plant a zero-test green -> it is REFUSED (no_result).
- POSITIVE: plant a genuine passing run -> it is ACCEPTED (green).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REMEDIATION = Path(__file__).resolve().parents[1]
HISTORY = REMEDIATION.parents[0] / "history"
sys.path.insert(0, str(REMEDIATION))
sys.path.insert(0, str(HISTORY))

import nonzero_result
import protocol

# A real cargo test-target that COMPILED and ran but whose tests were all gated
# out (`--features notifier`): the binary runs, executes zero tests, exits 0.
GATED_ZERO_TEST = (
    "   Compiling reverie-notifier v0.1.0\n"
    "    Finished test [unoptimized + debuginfo] target(s)\n"
    "     Running unittests src/lib.rs (target/debug/deps/reverie_notifier-abc)\n"
    "\n"
    "running 0 tests\n"
    "\n"
    "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
)

# A genuine passing validate: many banners, real executed tests.
GENUINE_PASS = (
    "     Running unittests src/lib.rs (target/debug/deps/detcore-111)\n"
    "\n"
    "running 47 tests\n"
    "test tests::a ... ok\n"
    "test result: ok. 47 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
    "     Running unittests src/main.rs (target/debug/deps/hermit-222)\n"
    "running 0 tests\n"
    "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
    "✅ Validation summary [full] (12 passed, 0 failed)\n"
)


class ExecutedTestCountTest(unittest.TestCase):
    def test_no_banner_is_unknown_not_zero(self) -> None:
        # UNKNOWN must be distinct from zero: a log with no test-runner banner
        # (empty, or build-only) is None, never 0 — so it is never downgraded.
        self.assertIsNone(nonzero_result.executed_test_count(""))
        self.assertIsNone(
            nonzero_result.executed_test_count("Finished. everything compiled.")
        )

    def test_all_zero_banners_sum_to_zero(self) -> None:
        self.assertEqual(nonzero_result.executed_test_count(GATED_ZERO_TEST), 0)

    def test_real_banners_sum_executed(self) -> None:
        # 47 + 0 across two binaries; the zero-test binary does not mask the real
        # one, so the run as a whole executed tests.
        self.assertEqual(nonzero_result.executed_test_count(GENUINE_PASS), 47)

    def test_singular_running_one_test(self) -> None:
        self.assertEqual(
            nonzero_result.executed_test_count("running 1 test\n"), 1
        )

    def test_passed_count_fallback_when_running_banner_absent(self) -> None:
        self.assertEqual(
            nonzero_result.executed_test_count("test result: ok. 5 passed; 0 failed"),
            5,
        )


class IsZeroTestGreenTest(unittest.TestCase):
    def test_negative_zero_test_green_is_refused(self) -> None:
        self.assertTrue(nonzero_result.is_zero_test_green(GATED_ZERO_TEST))

    def test_positive_genuine_pass_is_accepted(self) -> None:
        self.assertFalse(nonzero_result.is_zero_test_green(GENUINE_PASS))

    def test_unknown_log_is_not_flagged(self) -> None:
        # Fail-safe: a green whose log carries no banner is NOT flagged.
        self.assertFalse(nonzero_result.is_zero_test_green(""))
        self.assertFalse(nonzero_result.is_zero_test_green("built ok, no tests logged"))


class ClassifyLocalZeroTestTest(unittest.TestCase):
    """The detector wired into the speculative-land local classifier."""

    def test_negative_zero_test_green_downgrades_to_no_result(self) -> None:
        state, reason = protocol._classify_local(0, GATED_ZERO_TEST)
        self.assertEqual(state, "no_result")
        self.assertEqual(reason, "zero-test-green")

    def test_positive_genuine_pass_stays_green(self) -> None:
        state, reason = protocol._classify_local(0, GENUINE_PASS)
        self.assertEqual(state, "green")
        self.assertEqual(reason, "clean exit")

    def test_clean_exit_without_captured_output_stays_green(self) -> None:
        # Backward-compatible: exit 0 with no readable output is still green — the
        # detector never manufactures a no_result from a log it could not read.
        self.assertEqual(protocol._local_state(0), "green")
        self.assertEqual(protocol._local_state(0, ""), "green")

    def test_downgrade_is_no_result_never_red(self) -> None:
        # Even a false positive is recoverable: a zero-test green becomes a hole
        # to re-dispatch, never a tip to revert.
        state, _ = protocol._classify_local(0, GATED_ZERO_TEST)
        self.assertNotEqual(state, "red")


if __name__ == "__main__":
    unittest.main()

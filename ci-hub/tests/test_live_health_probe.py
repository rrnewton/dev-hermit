#!/usr/bin/env python3
"""Tests for the live probe's tool-vs-service result classification."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_health_probe import classify

COST = "# ci-hub/health tool COST ACTUAL wall=1.000s cpu=0.100s"
SECTIONS = "GitHub main health: GREEN\nCI health: HEALTHY"
RED_SECTIONS = "HARD WARNING: GITHUB MAIN IS RED\nCI health: HEALTHY"


class LiveHealthProbeTest(unittest.TestCase):
    def test_healthy_or_red_live_state_means_tool_worked(self) -> None:
        for returncode in (0, 1):
            self.assertTrue(classify(returncode, f"{SECTIONS}\n{COST}")[0])

    def test_canonical_red_heading_is_a_live_health_section(self) -> None:
        output = f"orphaned-task-detector: 'tg' not on PATH\n{RED_SECTIONS}\n{COST}"
        accepted, reason = classify(1, output)
        self.assertTrue(accepted)
        self.assertIn("authoritative", reason)

    def test_red_heading_does_not_replace_other_required_evidence(self) -> None:
        red_without_ci = f"HARD WARNING: GITHUB MAIN IS RED\n{COST}"
        self.assertFalse(classify(1, red_without_ci)[0])
        self.assertFalse(classify(1, RED_SECTIONS)[0])

    def test_explicit_partial_service_result_is_not_tool_failure(self) -> None:
        output = f"GitHub main health: DEGRADED\nUNAVAILABLE\nCI health: DEGRADED\nPARTIAL RESULT\n{COST}"
        accepted, reason = classify(2, output)
        self.assertTrue(accepted)
        self.assertIn("bounded partial", reason)

    def test_crash_or_missing_cost_is_tool_failure(self) -> None:
        self.assertFalse(classify(2, f"{SECTIONS}\ntraceback")[0])
        self.assertFalse(classify(3, f"{SECTIONS}\n{COST}")[0])


if __name__ == "__main__":
    unittest.main()

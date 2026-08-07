#!/usr/bin/env python3
"""Both-direction fixtures for the validate CPU ratchet.

Negatives alone would be satisfied by a tool that flags everything, so each is
paired with a positive that must stay quiet. Every fixture is a tmpdir ledger;
the real ledger is never read or written.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "validate_time_ratchet.py"
SPEC = importlib.util.spec_from_file_location("vtr", MODULE)
assert SPEC and SPEC.loader
vtr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vtr)


def ledger(tmp: str, cpus: list[float], profile: str = "full") -> Path:
    p = Path(tmp) / "ledger.jsonl"
    with p.open("w") as f:
        for c in cpus:
            f.write(json.dumps({"profile": profile, "user_seconds": c * 0.8,
                                "sys_seconds": c * 0.2, "real_seconds": c / 4}) + "\n")
    return p


class RatchetTest(unittest.TestCase):
    def test_positive_a_normal_run_is_not_flagged(self):
        """Load-bearing: a tool that alarms on ordinary runs gets muted."""
        with tempfile.TemporaryDirectory() as tmp:
            led = ledger(tmp, [100.0] * 40)
            self.assertEqual(0, vtr.main(["--ledger", str(led), "--cpu-seconds", "105", "--gate"]))

    def test_negative_a_regressed_run_is_flagged_and_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = ledger(tmp, [100.0] * 40)
            self.assertEqual(1, vtr.main(["--ledger", str(led), "--cpu-seconds", "500", "--gate"]))

    def test_gate_is_opt_in_so_the_default_cannot_block_landing(self):
        """Same regressed input, no --gate: reports but exits 0."""
        with tempfile.TemporaryDirectory() as tmp:
            led = ledger(tmp, [100.0] * 40)
            self.assertEqual(0, vtr.main(["--ledger", str(led), "--cpu-seconds", "500"]))

    def test_too_few_samples_refuses_rather_than_inventing_a_threshold(self):
        """A p90 over 3 runs is a number with no authority behind it."""
        with tempfile.TemporaryDirectory() as tmp:
            led = ledger(tmp, [100.0] * 3)
            self.assertEqual(2, vtr.main(["--ledger", str(led), "--cpu-seconds", "500", "--gate"]))

    def test_profiles_are_not_mixed_into_one_baseline(self):
        """`quick` and `full` are different workloads; pooling them is meaningless.

        40 cheap `quick` runs plus 40 expensive `full` runs: a 500s full run must
        be judged against the full baseline (ok), not against the pooled one.
        """
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "l.jsonl"
            with p.open("w") as f:
                for _ in range(40):
                    f.write(json.dumps({"profile": "quick", "user_seconds": 8,
                                        "sys_seconds": 2, "real_seconds": 3}) + "\n")
                for _ in range(40):
                    f.write(json.dumps({"profile": "full", "user_seconds": 400,
                                        "sys_seconds": 100, "real_seconds": 120}) + "\n")
            self.assertEqual(0, vtr.main(["--ledger", str(p), "--profile", "full",
                                          "--cpu-seconds", "500", "--gate"]))
            # Judged against the cheap quick baseline, the same number IS a regression.
            self.assertEqual(1, vtr.main(["--ledger", str(p), "--profile", "quick",
                                          "--cpu-seconds", "500", "--gate"]))

    def test_rows_without_timing_do_not_enter_the_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "l.jsonl"
            with p.open("w") as f:
                for _ in range(40):
                    f.write(json.dumps({"profile": "full", "user_seconds": 80,
                                        "sys_seconds": 20}) + "\n")
                for _ in range(20):
                    f.write(json.dumps({"profile": "full"}) + "\n")  # untimed
            rows = vtr.load(p, "full")
            self.assertEqual(60, len(rows))
            self.assertEqual(40, vtr.baseline(rows)["n"], "untimed rows must be excluded")


if __name__ == "__main__":
    unittest.main()

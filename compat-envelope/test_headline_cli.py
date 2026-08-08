#!/usr/bin/env python3
"""End-to-end bracket for the emitter-facing CLI.

`test_headline.py` proves the RULE inside the library. This file proves the rule
survives the SUBPROCESS BOUNDARY an emitter has to cross, because all three
emission sites are `rust-script` and can only reach the rule by executing it.
A library that passes its own tests and cannot be invoked provides exactly the
protection of no library at all --- which is the defect class this work exists
to close, one level up.

Every case runs the CLI as a real child process, not by importing it.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parent / "headline_cli.py"


def call(*specs: str) -> tuple[int, str, str]:
    argv = [sys.executable, str(CLI)]
    for s in specs:
        argv += ["--headline", s]
    r = subprocess.run(argv, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


class CliBoundary(unittest.TestCase):
    # --- positive controls: without these the refusals below prove nothing ----

    def test_full_clean_sweep_renders_and_is_not_a_no_result(self):
        rc, out, _ = call("ptrace:72:72:72")
        self.assertEqual(rc, 0)
        self.assertNotIn("NO-RESULT", out)
        self.assertIn("100% of 72 executed", out)

    def test_cli_is_actually_invokable_as_a_child_process(self):
        """The whole point: an emitter can only reach the rule by executing it."""
        rc, out, _ = call("dbi:8:8:72")
        self.assertEqual(rc, 0)
        self.assertTrue(out, "CLI produced no stdout")

    # --- the five planted mutations, end to end ------------------------------

    def test_m1_omitted_executed_count_is_refused(self):
        rc, _, err = call("dbi:8:72")          # 3 fields: EXECUTED dropped
        self.assertEqual(rc, 2)
        self.assertIn("EXECUTED", err)

    def test_m2_zero_executed_cannot_render_as_a_score(self):
        rc, out, _ = call("sabre:0:0:72")
        self.assertEqual(rc, 0)
        self.assertIn("NO-RESULT", out)
        self.assertNotIn("%", out.splitlines()[0])

    def test_m3_partial_sweep_shows_its_shortfall(self):
        """8/8 passing out of 72 must not read like 72/72."""
        rc, out, _ = call("dbi:8:8:72")
        self.assertEqual(rc, 0)
        self.assertIn("64 unmeasured", out)
        self.assertIn("100% of 8 executed", out)

    def test_m4_passed_exceeding_executed_is_refused(self):
        rc, _, err = call("bad:9:8:72")
        self.assertEqual(rc, 2)
        self.assertIn("cannot pass without running", err)

    def test_m5_executed_exceeding_denominator_is_refused(self):
        rc, _, err = call("bad:8:80:72")
        self.assertEqual(rc, 2)
        self.assertIn("exceeds denominator", err)

    # --- the assertion the wiring task exists to preserve --------------------

    def test_measured_nothing_and_everything_differ_across_the_boundary(self):
        _, nothing, _ = call("x:0:0:72")
        _, everything, _ = call("x:72:72:72")
        self.assertNotEqual(nothing, everything)

    def test_empty_summary_is_refused_not_printed_as_a_clean_total(self):
        rc, out, _ = call()
        self.assertEqual(rc, 2, f"empty summary rendered instead of refusing: {out!r}")


if __name__ == "__main__":
    unittest.main()

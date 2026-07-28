#!/usr/bin/env python3
"""Regression tests for shared QEMU demo reporting."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from demo_common import compare_runs, print_comparison


class ComparisonTests(unittest.TestCase):
    def test_matching_run_reports_success(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            print_comparison(
                True,
                [
                    "PASS: QEMU argv matches anchor",
                    "PASS: qcow2 SHA-256 matches (stable-sha)",
                    "PASS: normalized Hermit INFO log matches",
                ],
                "stable-sha",
                "Boot",
            )
        self.assertIn("PASS: all repeat checks match the first run", output.getvalue())
        self.assertIn("DETERMINISTIC!", output.getvalue())
        self.assertNotIn("ERROR", output.getvalue())

    def test_qcow2_divergence_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            info_log = Path(directory) / "hermit-info.log"
            info_log.write_text("stable log\n")
            anchor = {
                "qemu_argv": ["qemu-system-x86_64"],
                "qcow2_sha256": "anchor-sha",
                "info_log": str(info_log),
            }
            current = {
                "qemu_argv": ["qemu-system-x86_64"],
                "qcow2_sha256": "diverged-sha",
                "info_log": str(info_log),
            }

            passed, report = compare_runs(anchor, current)

        self.assertFalse(passed)
        self.assertTrue(
            any(line.startswith("ERROR: qcow2 SHA-256 differs") for line in report)
        )
        output = StringIO()
        with self.assertRaisesRegex(RuntimeError, "run diverged from anchor"):
            with redirect_stdout(output):
                print_comparison(passed, report, current["qcow2_sha256"], "Boot")
        self.assertIn("ERROR: RUN DIVERGED FROM ANCHOR", output.getvalue())
        self.assertNotIn("SUCCESS", output.getvalue())


if __name__ == "__main__":
    unittest.main()

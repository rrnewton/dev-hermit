#!/usr/bin/env python3
"""Regression tests for shared QEMU demo comparisons."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from demo_common import compare_runs, hermit_log_diff, print_comparison


class HeapPointerNormalizationTests(unittest.TestCase):
    def test_host_heap_pointer_difference_reports_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anchor_log = root / "anchor.log"
            current_log = root / "current.log"
            anchor_log.write_text(
                "2026-07-28T11:24:02.290216Z  INFO detcore::tool_global: "
                "thread proceeds via <ivar 0x562b103a32d0 Go(None)>\n"
            )
            current_log.write_text(
                "2026-07-28T11:27:49.481363Z  INFO detcore::tool_global: "
                "thread proceeds via <ivar 0x5602a73192d0 Go(None)>\n"
            )
            common = {
                "qemu_argv": ["qemu-system-x86_64"],
                "qcow2_sha256": "stable-qcow2",
                "serial_sha256": "stable-serial",
            }
            anchor = dict(common, info_log=str(anchor_log))
            current = dict(common, info_log=str(current_log))

            passed, report = compare_runs(anchor, current)

        self.assertTrue(passed, report)
        output = StringIO()
        with redirect_stdout(output):
            print_comparison(passed, report, current["qcow2_sha256"], "Boot")
        self.assertIn("PASS: all repeat checks match the first run", output.getvalue())
        self.assertNotIn("PARTIAL", output.getvalue())

    def test_non_host_hex_difference_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anchor_log = root / "anchor.log"
            current_log = root / "current.log"
            anchor_log.write_text("INFO guest instruction pointer 0x1234\n")
            current_log.write_text("INFO guest instruction pointer 0x5678\n")

            difference = hermit_log_diff(anchor_log, current_log)

        self.assertIn("first normalized divergence at line 1", difference)
        self.assertIn("0x1234", difference)
        self.assertIn("0x5678", difference)


if __name__ == "__main__":
    unittest.main()

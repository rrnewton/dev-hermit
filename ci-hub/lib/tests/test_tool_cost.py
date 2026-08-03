from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "ci-hub/bin/tool-cost"


class ToolCostTest(unittest.TestCase):
    def run_tool(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(TOOL),
                "--tool",
                "test/tool",
                "--estimate-wall-seconds",
                "12",
                "--estimate-cpu-seconds",
                "3",
                "--basis",
                "3 items x 4 seconds",
                "--",
                *command,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_success_reports_estimate_and_actual(self) -> None:
        result = self.run_tool([sys.executable, "-c", "print('payload')"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "payload\n")
        self.assertIn("COST ESTIMATE tool=test/tool wall=12.000s cpu=3.000s", result.stderr)
        self.assertIn("basis='3 items x 4 seconds'", result.stderr)
        self.assertRegex(
            result.stderr,
            r"COST ACTUAL tool=test/tool wall=[0-9.]+s cpu=[0-9.]+s .* exit=0",
        )

    def test_failure_preserves_exit_and_reports_actual(self) -> None:
        result = self.run_tool([sys.executable, "-c", "raise SystemExit(7)"])
        self.assertEqual(result.returncode, 7)
        self.assertIn("COST ESTIMATE", result.stderr)
        self.assertIn("COST ACTUAL", result.stderr)
        self.assertIn("exit=7", result.stderr)

    def test_launch_failure_still_reports_actual(self) -> None:
        result = self.run_tool(["/definitely/not/a/command"])
        self.assertEqual(result.returncode, 127)
        self.assertIn("cannot launch", result.stderr)
        self.assertIn("COST ACTUAL", result.stderr)
        self.assertIn("exit=127", result.stderr)


if __name__ == "__main__":
    unittest.main()

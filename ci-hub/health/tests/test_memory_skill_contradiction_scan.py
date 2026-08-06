#!/usr/bin/env python3
"""Controls for optional Claude-memory absence on stock Codex/CI hosts."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCANNER = ROOT / "scripts" / "memory-skill-contradiction-scan.rs"


class MemorySkillContradictionScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("rust-script") is None:
            raise unittest.SkipTest("rust-script unavailable")

    def run_scanner(self, *args: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "no-claude-memory-here"
            env = dict(os.environ, HERMIT_MEMORY_DIR=str(missing))
            return subprocess.run(
                [str(SCANNER), *args],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )

    def test_gate_treats_absent_memory_as_empty_advisory_input(self) -> None:
        result = self.run_scanner("--gate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("state=ok", result.stdout)
        self.assertIn("contradictions=0", result.stdout)
        self.assertIn("drift=0", result.stdout)
        self.assertNotIn("memory-dir-missing", result.stdout + result.stderr)

    def test_human_and_list_modes_still_diagnose_absence(self) -> None:
        for args in ((), ("--list",)):
            with self.subTest(args=args):
                result = self.run_scanner(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("memory dir not found", result.stderr)


if __name__ == "__main__":
    unittest.main()

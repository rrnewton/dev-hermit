#!/usr/bin/env python3
"""Controls for optional Claude-memory absence on stock Codex/CI hosts."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SCANNER = ROOT / "scripts" / "memory-skill-contradiction-scan.rs"
sys.path.insert(0, str(ROOT / "ci-hub" / "health"))
import operational_health  # noqa: E402


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

    def test_real_repository_contradiction_reaches_operational_gate(self) -> None:
        """Plant inert repository text, never a label or authorization."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "dev-hermit"
            (root / ".claude/skills/planted-contradiction").mkdir(parents=True)
            (root / "ci-hub/health").mkdir(parents=True)
            (root / "hermit").mkdir()
            (root / "reverie").mkdir()
            (root / ".gitmodules").write_text("")
            (root / ".claude/skills/planted-contradiction/SKILL.md").write_text(
                "---\n"
                "name: planted-contradiction\n"
                "description: Inert contradiction fixture.\n"
                "---\n\n"
                "# Fixture\n\nThis stale text says main is unprotected.\n"
            )
            (root / "ci-hub/health/skill-contradiction-denylist.txt").write_text(
                "skill | main+unprotected | planted contradiction\n"
            )
            missing_memory = root / "no-claude-memory"
            env = dict(os.environ, HERMIT_MEMORY_DIR=str(missing_memory))

            scan = subprocess.run(
                [str(SCANNER), "--gate"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            self.assertEqual(scan.returncode, 1, scan.stdout + scan.stderr)
            self.assertIn("state=contradiction", scan.stdout)
            self.assertIn("contradictions=1", scan.stdout)
            self.assertIn("planted-contradiction", scan.stdout)

            output = io.StringIO()
            with mock.patch.object(operational_health, "ROOT", root), mock.patch.dict(
                os.environ, {"HERMIT_MEMORY_DIR": str(missing_memory)}
            ), contextlib.redirect_stdout(output):
                status = operational_health.memory_skill_sync_gate()
            self.assertEqual(status, 1)
            self.assertIn("state=contradiction", output.getvalue())
            self.assertIn("contradictions=1", output.getvalue())
            self.assertIn("planted-contradiction", output.getvalue())


if __name__ == "__main__":
    unittest.main()

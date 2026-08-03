#!/usr/bin/env python3
"""Unit tests for the ci-hub README command extractor."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("documented_commands.py")
SPEC = importlib.util.spec_from_file_location("documented_commands", MODULE_PATH)
assert SPEC and SPEC.loader
documented_commands = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = documented_commands
SPEC.loader.exec_module(documented_commands)


class DocumentedCommandsTest(unittest.TestCase):
    def test_repository_inventory_is_complete_and_classified(self) -> None:
        commands = documented_commands.extract_commands()
        self.assertEqual(len(commands), documented_commands.EXPECTED_COMMANDS)
        self.assertEqual(
            {command.mode for command in commands},
            {"setup", "parse", "local-read", "live-read"},
        )
        self.assertTrue(any("land_and_arm.py" in command.text for command in commands))

    def test_unclassified_command_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "README.md"
            path.write_text("```bash\n./not-a-real-tool --quiet\n```\n")
            with self.assertRaises(documented_commands.DocsCommandError):
                documented_commands.extract_commands((path,))

    def test_cost_lines_do_not_count_as_domain_output(self) -> None:
        output = (
            "COST ESTIMATE tool=x wall=unknown cpu=unknown basis='unknown'\n"
            "COST ACTUAL tool=x wall=0.1s cpu=0.1s exit=0\n"
        )
        self.assertEqual(documented_commands._business_output(output), "")


if __name__ == "__main__":
    unittest.main()

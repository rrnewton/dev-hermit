#!/usr/bin/env python3
"""Unit tests for the ci-hub README command extractor."""

from __future__ import annotations

import importlib.util
import os
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
        self.assertTrue(
            any(
                "directives/check.py --quickstart" in command.text
                for command in commands
            )
        )
        self.assertTrue(any("ci-hub/ci-hub quickstart" in command.text for command in commands))
        self.assertTrue(any("systemd-run --user" in command.text for command in commands))
        parent_hosted = [
            command
            for command in commands
            if "hosted-status --repo rrnewton/dev-hermit" in command.text
        ]
        self.assertEqual(len(parent_hosted), 1)
        self.assertEqual(parent_hosted[0].mode, "live-read")
        self.assertIn("a" * 40, documented_commands._render(parent_hosted[0].text))
        reconcile_commands = [
            command
            for command in commands
            if command.text.startswith("./ci-hub/bin/reconcile-receipts")
        ]
        self.assertEqual(len(reconcile_commands), 3)
        self.assertEqual({command.mode for command in reconcile_commands}, {"live-read"})

    def test_systemd_activation_is_syntax_only(self) -> None:
        command = "systemd-run --user --unit=fixture /bin/true"
        self.assertEqual(documented_commands._classify(command), "parse")
        self.assertEqual(documented_commands._parse_probe(command), "systemd-run --help")

    def test_reconcile_receipts_is_a_live_read_with_exact_binary_identity(self) -> None:
        self.assertEqual(
            documented_commands._classify(
                "./ci-hub/bin/reconcile-receipts # human table"
            ),
            "live-read",
        )
        with self.assertRaises(documented_commands.DocsCommandError):
            documented_commands._classify(
                "./ci-hub/bin/reconcile-receipts-untrusted # human table"
            )

    def test_reconcile_receipts_non_live_mode_executes_only_help(self) -> None:
        command = documented_commands.DocumentedCommand(
            documented_commands.ROOT / "ci-hub/README.md",
            73,
            "./ci-hub/bin/reconcile-receipts --json",
            "live-read",
        )
        self.assertEqual(
            documented_commands._parse_probe(command.text),
            "./ci-hub/bin/reconcile-receipts --help",
        )
        reports = documented_commands._run_one(
            command,
            root=documented_commands.ROOT,
            environment=os.environ.copy(),
            live=False,
            verify_purity=False,
        )
        self.assertIn("PASS live-read", reports[0])

    def test_unclassified_command_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "README.md"
            path.write_text("```bash\n./not-a-real-tool --quiet\n```\n")
            with self.assertRaises(documented_commands.DocsCommandError):
                documented_commands.extract_commands((path,))

    def test_cost_lines_do_not_count_as_domain_output(self) -> None:
        output = (
            "# x tool COST ESTIMATE wall=unknown cpu=unknown basis='unknown'\n"
            "# x tool COST ACTUAL wall=0.1s cpu=0.1s exit=0\n"
        )
        self.assertEqual(documented_commands._business_output(output), "")

    def test_mtime_only_change_is_a_purity_failure(self) -> None:
        self.assertEqual(
            documented_commands._changed_mtimes({"a": 1, "b": 2}, {"a": 3, "b": 2}),
            ["a"],
        )

    def test_tg_quickstart_contract_rejects_home_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "tg"
            binary.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' 'TaskGraph agent quickstart' "
                "'tg claim TASK_ID' 'tg note TASK_ID' 'TG_DB_PATH'\n"
            )
            binary.chmod(0o755)
            reports = documented_commands._run_tg_quickstart(
                str(binary),
                root=documented_commands.ROOT,
                environment=os.environ.copy(),
                verify_purity=False,
            )
            self.assertIn("PASS quickstart tg", reports[0])

            binary.write_text(binary.read_text() + "touch \"$HOME/side-effect\"\n")
            with self.assertRaises(documented_commands.DocsCommandError):
                documented_commands._run_tg_quickstart(
                    str(binary),
                    root=documented_commands.ROOT,
                    environment=os.environ.copy(),
                    verify_purity=False,
                )

    def test_closeout_requires_push_and_dirty_tree_accounting(self) -> None:
        with self.assertRaises(documented_commands.DocsCommandError):
            documented_commands._evaluate_closeout(
                head="a" * 40,
                origin_main="b" * 40,
                unpushed=1,
                dirty="",
                dirty_note=None,
            )
        with self.assertRaises(documented_commands.DocsCommandError):
            documented_commands._evaluate_closeout(
                head="a" * 40,
                origin_main="a" * 40,
                unpushed=0,
                dirty=" M concurrent.txt",
                dirty_note=None,
            )
        reports = documented_commands._evaluate_closeout(
            head="a" * 40,
            origin_main="a" * 40,
            unpushed=0,
            dirty=" M concurrent.txt",
            dirty_note="concurrent.txt is owned by hermit-226 and intentionally left unchanged",
        )
        self.assertIn("concurrent.txt", "\n".join(reports))


if __name__ == "__main__":
    unittest.main()

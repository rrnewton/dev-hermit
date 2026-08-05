#!/usr/bin/env python3
"""Tests for the owner tooling directive ancestry gate."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "check.py"
SPEC = importlib.util.spec_from_file_location("directive_check", MODULE_PATH)
assert SPEC and SPEC.loader
directive_check = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = directive_check
SPEC.loader.exec_module(directive_check)


def completed(command, rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, rc, stdout, stderr)


class FakeRunner:
    def __init__(self, known_tasks: set[str]):
        self.known_tasks = known_tasks
        self.verifier_calls: list[str] = []

    def __call__(self, command, **_kwargs):
        command = tuple(str(item) for item in command)
        if command[:2] == ("tg", "sql"):
            return completed(command, stdout="\n".join(sorted(self.known_tasks)) + "\n")
        if "protocol.py" in " ".join(command):
            identity = command[command.index("verify-landing") + 1]
            self.verifier_calls.append(identity)
            if identity.startswith("a"):
                payload = {
                    "state": "landed",
                    "ancestry": "ancestor",
                    "resolved_sha": identity,
                }
                return completed(command, stdout=json.dumps(payload))
            if identity.startswith("b"):
                payload = {
                    "state": "not-landed",
                    "ancestry": "not-ancestor",
                    "resolved_sha": identity,
                }
                return completed(command, rc=1, stdout=json.dumps(payload))
            payload = {"state": "unverifiable", "reason": "no mergeCommit.oid"}
            return completed(command, rc=2, stdout=json.dumps(payload))
        if command[:3] == ("git", "-C", str(directive_check.ROOT)) or (
            len(command) > 3 and command[0] == "git" and command[1] == "-C"
        ):
            return completed(command, stdout="d" * 40 + "\n")
        raise AssertionError(f"unexpected command: {command}")


def directive(
    item_id: str,
    *,
    identity: str | None,
    task: str | None = None,
    owner: str = "agent",
    parent_id: str | None = None,
):
    return {
        "id": item_id,
        "summary": item_id.replace("-", " "),
        "requested_at": "2026-08-04",
        "repository": "rrnewton/dev-hermit",
        "checkout": ".",
        "target": "main",
        "task": task if task is not None else f"task-{item_id}",
        "owner": owner,
        "source_row": item_id,
        "parent_id": parent_id,
        "implementation": None
        if identity is None
        else {"kind": "commit", "identity": identity},
    }


class DirectiveCheckTest(unittest.TestCase):
    def evaluate(self, directives):
        tasks = {item["task"] for item in directives if item.get("task")}
        runner = FakeRunner(tasks)
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "ledger.json"
            ledger.write_text(json.dumps({"schema_version": 1, "directives": directives}))
            report = directive_check.evaluate(
                ledger_path=ledger,
                run=runner,
                checked_at="2026-08-04T00:00:00+00:00",
            )
        return report, runner

    def test_planted_nonancestor_is_refused_and_three_landed_stay_satisfied(self):
        directives = [
            directive("landed-one", identity="a" * 40),
            directive("landed-two", identity="a" * 39 + "1"),
            directive("landed-three", identity="a" * 39 + "2"),
            directive("planted-not-landed", identity="b" * 40),
        ]
        report, runner = self.evaluate(directives)

        self.assertEqual(1, report.exit_code)
        self.assertEqual(3, report.counts["satisfied"])
        self.assertEqual(1, report.counts["not_landed"])
        planted = next(
            item for item in report.directives if item.id == "planted-not-landed"
        )
        self.assertEqual("not-ancestor", planted.ancestry)
        self.assertEqual(4, len(runner.verifier_calls))

    def test_missing_task_and_owner_cannot_be_satisfied(self):
        directives = [
            directive("missing-task", identity="a" * 40, task=""),
            directive("missing-owner", identity="a" * 39 + "1", owner=""),
        ]
        report, _runner = self.evaluate(directives)

        self.assertEqual(1, report.exit_code)
        self.assertEqual(1, report.counts["missing_task"])
        self.assertEqual(1, report.counts["missing_owner"])
        self.assertEqual(0, report.counts.get("satisfied", 0))
        self.assertEqual(1, report.issue_counts["missing_task"])
        self.assertEqual(1, report.issue_counts["missing_owner"])

    def test_landed_parent_stays_partial_while_child_has_no_implementation(self):
        directives = [
            directive("parent", identity="a" * 40),
            directive("child", identity=None, parent_id="parent"),
        ]
        report, _runner = self.evaluate(directives)

        states = {item.id: item.state for item in report.directives}
        self.assertEqual({"parent": "partial", "child": "open"}, states)
        self.assertEqual(1, report.issue_counts["no_implementation"])

    def test_documentation_without_identity_remains_open(self):
        report, runner = self.evaluate([directive("quoted-only", identity=None)])

        self.assertEqual(1, report.exit_code)
        self.assertEqual(1, report.counts["open"])
        self.assertEqual([], runner.verifier_calls)


if __name__ == "__main__":
    unittest.main()

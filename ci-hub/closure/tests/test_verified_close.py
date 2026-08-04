#!/usr/bin/env python3
"""Fixture-only tests for the task-closure evidence gate."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "verified_close.py"
SPEC = importlib.util.spec_from_file_location("verified_close", MODULE_PATH)
assert SPEC and SPEC.loader
verified_close = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verified_close
SPEC.loader.exec_module(verified_close)


def completed(command, rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, rc, stdout, stderr)


class FakeRunner:
    def __init__(self, code_state="landed"):
        self.code_state = code_state
        self.task_mutations: list[tuple[str, ...]] = []

    def __call__(self, command, **_kwargs):
        command = tuple(str(item) for item in command)
        if "protocol.py" in " ".join(command):
            if self.code_state == "landed":
                payload = {
                    "state": "landed",
                    "rc": 0,
                    "resolved_sha": "a" * 40,
                }
                return completed(command, stdout=json.dumps(payload))
            if self.code_state == "not-landed":
                payload = {
                    "state": "not-landed",
                    "rc": 1,
                    "resolved_sha": "b" * 40,
                }
                return completed(command, rc=1, stdout=json.dumps(payload))
            payload = {
                "state": "unverifiable",
                "rc": 2,
                "reason": "no mergeCommit.oid",
            }
            return completed(command, rc=2, stdout=json.dumps(payload))
        if command[:4] == ("with-proxy", "gh", "run", "view"):
            run_id = command[4]
            payload = {
                "databaseId": int(run_id),
                "url": f"https://github.test/actions/runs/{run_id}",
                "status": "completed",
                "conclusion": "success",
            }
            return completed(command, stdout=json.dumps(payload))
        if command[:4] == ("git", "-C", str(verified_close.ROOT), "ls-files"):
            return completed(command, stdout="AGENTS.md\n")
        if command[:3] == ("with-proxy", "git", "-C"):
            return completed(command)
        if command[:5] == (
            "git",
            "-C",
            str(verified_close.ROOT),
            "cat-file",
            "-e",
        ):
            return completed(command)
        if command[:4] == ("git", "-C", str(verified_close.ROOT), "rev-parse"):
            return completed(command, stdout="c" * 40 + "\n")
        if command and command[0] == "tg":
            self.task_mutations.append(command)
            return completed(command)
        raise AssertionError(f"unexpected command: {command}")


class VerifiedCloseTest(unittest.TestCase):
    def test_unverifiable_reference_is_refused_without_task_mutation(self):
        runner = FakeRunner(code_state="unverifiable")
        rc = verified_close.main(
            ["fixture-task", "--code", "123", "--source", "."], run=runner
        )
        self.assertEqual(verified_close.UNVERIFIABLE, rc)
        self.assertEqual([], runner.task_mutations)

    def test_refused_and_unverifiable_are_distinct(self):
        refused = FakeRunner(code_state="not-landed")
        unverifiable = FakeRunner(code_state="unverifiable")
        self.assertEqual(
            verified_close.REFUSED,
            verified_close.main(
                ["fixture-task", "--code", "b" * 40, "--source", "."],
                run=refused,
            ),
        )
        self.assertEqual(
            verified_close.UNVERIFIABLE,
            verified_close.main(
                ["fixture-task", "--code", "123", "--source", "."],
                run=unverifiable,
            ),
        )
        self.assertEqual([], refused.task_mutations)
        self.assertEqual([], unverifiable.task_mutations)

    def test_three_legitimate_fixture_closures_succeed(self):
        runner = FakeRunner()
        cases = (
            ["code-task", "--code", "123", "--source", "."],
            ["artifact-task", "--artifact", "AGENTS.md"],
            ["run-task", "--run-id", "987", "--repo", "rrnewton/hermit"],
        )
        results = [verified_close.main(case, run=runner) for case in cases]

        self.assertEqual([0, 0, 0], results)
        notes = [command for command in runner.task_mutations if command[1] == "note"]
        closes = [command for command in runner.task_mutations if command[1] == "update"]
        self.assertEqual(3, len(notes))
        self.assertEqual(3, len(closes))
        self.assertTrue(all("CLOSURE-VERIFIED:" in command[3] for command in notes))
        self.assertTrue(all(command[-2:] == ("--status", "closed") for command in closes))
        self.assertEqual(
            ["note", "update"] * 3,
            [command[1] for command in runner.task_mutations],
        )


if __name__ == "__main__":
    unittest.main()

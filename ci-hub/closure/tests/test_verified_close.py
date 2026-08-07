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
    def __init__(
        self,
        code_state="landed",
        *,
        artifact_present=True,
        artifact_type="blob",
        artifact_ancestry_rc=0,
    ):
        self.code_state = code_state
        self.artifact_present = artifact_present
        self.artifact_type = artifact_type
        self.artifact_ancestry_rc = artifact_ancestry_rc
        self.task_mutations: list[tuple[str, ...]] = []
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command, **_kwargs):
        command = tuple(str(item) for item in command)
        self.commands.append(command)
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
        if command[:3] == ("with-proxy", "git", "-C"):
            return completed(command)
        if command[:5] == (
            "git",
            "-C",
            str(verified_close.ROOT),
            "cat-file",
            "-t",
        ):
            if not self.artifact_present:
                return completed(
                    command, rc=1, stderr="fatal: path does not exist in 'origin/main'"
                )
            return completed(command, stdout=self.artifact_type + "\n")
        if command[:4] == ("git", "-C", str(verified_close.ROOT), "log"):
            return completed(command, stdout="d" * 40 + "\n")
        if command[:4] == (
            "git",
            "-C",
            str(verified_close.ROOT),
            "merge-base",
        ):
            return completed(command, rc=self.artifact_ancestry_rc)
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
            ["fixture-task", "--code", "123", "--repo", "rrnewton/hermit", "--source", "."],
            run=runner,
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
                ["fixture-task", "--code", "123", "--repo", "rrnewton/hermit", "--source", "."],
                run=unverifiable,
            ),
        )
        self.assertEqual([], refused.task_mutations)
        self.assertEqual([], unverifiable.task_mutations)

    def test_three_legitimate_fixture_closures_succeed(self):
        runner = FakeRunner()
        cases = (
            ["code-task", "--code", "123", "--repo", "rrnewton/hermit", "--source", "."],
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
        artifact_note = next(
            command[3]
            for command in notes
            if "kind=artifact" in command[3]
        )
        self.assertIn("rrnewton/dev-hermit:AGENTS.md@" + "d" * 40, artifact_note)
        self.assertIn("target=main@" + "c" * 40, artifact_note)

    def test_missing_or_nonancestral_artifact_never_mutates_task(self):
        missing = FakeRunner(artifact_present=False)
        nonancestral = FakeRunner(artifact_ancestry_rc=1)

        self.assertEqual(
            verified_close.REFUSED,
            verified_close.main(
                ["missing-artifact", "--artifact", "AGENTS.md"], run=missing
            ),
        )
        self.assertEqual(
            verified_close.REFUSED,
            verified_close.main(
                ["orphan-artifact", "--artifact", "AGENTS.md"],
                run=nonancestral,
            ),
        )
        self.assertEqual([], missing.task_mutations)
        self.assertEqual([], nonancestral.task_mutations)

    def test_artifact_absent_from_this_working_tree_still_verifies(self):
        # The regression this brackets: a parent artifact published the ONLY
        # safe way -- from a worktree off origin/main -- is absent from the
        # chronically-behind parent primary. Gating on the working tree refused
        # it with "artifact is not a file" even though it was tracked, pushed,
        # and ancestry-present. Authority is origin/main, not this checkout.
        relative = "ai_docs/deliberately-absent-from-this-working-tree.md"
        self.assertFalse(
            (verified_close.ROOT / relative).exists(),
            "fixture path must not exist locally or the test proves nothing",
        )
        runner = FakeRunner()

        rc = verified_close.main(["absent-artifact", "--artifact", relative], run=runner)

        self.assertEqual(verified_close.CLOSED, rc)
        note = next(
            command[3] for command in runner.task_mutations if command[1] == "note"
        )
        self.assertIn(f"rrnewton/dev-hermit:{relative}@" + "d" * 40, note)
        self.assertNotIn(
            "ls-files",
            " ".join(" ".join(command) for command in runner.commands),
            "the stale-index gate must be gone, not merely bypassed",
        )

    def test_directory_on_main_is_refused_not_closed(self):
        # `cat-file -e origin/main:<dir>` succeeds for a TREE. Existence alone
        # would let a caller close against a directory, so the type is checked.
        runner = FakeRunner(artifact_type="tree")

        rc = verified_close.main(["tree-artifact", "--artifact", "ai_docs"], run=runner)

        self.assertEqual(verified_close.REFUSED, rc)
        self.assertEqual([], runner.task_mutations)

    def test_bare_pr_number_without_explicit_repo_is_refused(self):
        # Every repository has a #56. `execute-ambiguous-zero-fix-order-a3-a4-first`
        # -- a PARENT-repo task about compat-envelope/render-scorecard.rs -- was
        # closed with a bare `--code 56`, which the defaults resolved against
        # rrnewton/hermit and matched "docs: add Hermit error catalog (#56)"
        # from three weeks earlier. Real ancestry, wrong repository.
        runner = FakeRunner()

        rc = verified_close.main(["parent-task", "--code", "56"], run=runner)

        self.assertEqual(verified_close.REFUSED, rc)
        self.assertEqual([], runner.task_mutations)
        self.assertEqual([], runner.commands, "refusal must precede any verifier call")

    def test_full_sha_without_repo_still_works(self):
        # A 40-hex SHA is self-identifying -- the verifier can only resolve it
        # where it exists -- so the stricter rule must NOT catch it, or every
        # existing SHA-based caller breaks.
        runner = FakeRunner()

        rc = verified_close.main(["sha-task", "--code", "a" * 40], run=runner)

        self.assertEqual(verified_close.CLOSED, rc)

    def test_closure_note_records_which_repository_was_verified(self):
        runner = FakeRunner()

        verified_close.main(
            ["repo-task", "--code", "a" * 40, "--repo", "rrnewton/dev-hermit"],
            run=runner,
        )

        note = next(
            command[3] for command in runner.task_mutations if command[1] == "note"
        )
        self.assertIn("rrnewton/dev-hermit@" + "a" * 40, note)

    def test_code_closure_note_is_readable_by_its_downstream_consumer(self):
        # The note is not just for humans: ci-hub/directives/tg_landed.py derives
        # landing state from it. Its extractor takes an explicit SHA token or a
        # typed `@sha` tuple, so the OLD bare `resolved=<40hex>` matched NEITHER
        # and yielded []. Code closures were recording a SHA the consumer could
        # not read. Bind the two here so the format cannot drift apart again.
        spec = importlib.util.spec_from_file_location(
            "tg_landed",
            Path(verified_close.__file__).resolve().parents[1] / "directives/tg_landed.py",
        )
        assert spec and spec.loader
        tg_landed = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = tg_landed
        spec.loader.exec_module(tg_landed)

        runner = FakeRunner()
        verified_close.main(
            ["consumer-task", "--code", "a" * 40, "--repo", "rrnewton/dev-hermit"],
            run=runner,
        )
        note = next(
            command[3] for command in runner.task_mutations if command[1] == "note"
        )

        shas, _ = tg_landed.extract_implementation_refs([note])
        self.assertEqual(["a" * 40], shas)

    def test_artifact_outside_the_workspace_is_refused(self):
        runner = FakeRunner()

        rc = verified_close.main(
            ["escaped-artifact", "--artifact", "/etc/hostname"], run=runner
        )

        self.assertEqual(verified_close.REFUSED, rc)
        self.assertEqual([], runner.task_mutations)
        self.assertEqual([], runner.commands, "refusal must precede any git call")


if __name__ == "__main__":
    unittest.main()

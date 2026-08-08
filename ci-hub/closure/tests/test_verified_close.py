#!/usr/bin/env python3
"""Fixture-only tests for the task-closure evidence gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
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
        observation_output="0\t0\n",
        observation_rc=0,
        tags="implemented",
    ):
        self.tags = tags
        self.code_state = code_state
        self.artifact_present = artifact_present
        self.artifact_type = artifact_type
        self.artifact_ancestry_rc = artifact_ancestry_rc
        self.observation_output = observation_output
        self.observation_rc = observation_rc
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
        if (
            Path(command[0]).name == "git"
            and command[1:]
            == (
                "-C",
                "hermit",
                "rev-list",
                "--left-right",
                "--count",
                "HEAD...origin/main",
            )
        ):
            return completed(
                command,
                rc=self.observation_rc,
                stdout=self.observation_output,
            )
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
        if command[:2] == ("tg", "show"):
            # A READ, not a mutation. Keeping it out of task_mutations is what
            # lets the "refused paths never touch the task" assertions stay
            # meaningful now that the gateway reads tags before closing.
            return completed(command, stdout=f"Status:    IN_PROGRESS\nTags:      {self.tags}\n")
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

    # CONTRACT CHANGE, 2026-08-06 (close-on-implemented + single drain). This
    # test previously asserted that a resolved-but-not-landed reference was
    # REFUSED, pairing it with the unverifiable case. Under the new rule those
    # two are no longer the same kind of thing: "not landed yet" is a VERIFIED
    # state that closes, while "did not resolve" still refuses. Rewritten in
    # place rather than deleted so the reversal is visible in review.
    def test_not_landed_closes_while_unverifiable_still_refuses(self):
        unlanded = FakeRunner(code_state="not-landed")
        unverifiable = FakeRunner(code_state="unverifiable")
        self.assertEqual(
            verified_close.CLOSED,
            verified_close.main(
                ["fixture-task", "--code", "b" * 40, "--source", "."],
                run=unlanded,
            ),
        )
        self.assertEqual(
            verified_close.UNVERIFIABLE,
            verified_close.main(
                ["fixture-task", "--code", "123", "--repo", "rrnewton/hermit", "--source", "."],
                run=unverifiable,
            ),
        )
        self.assertNotEqual([], unlanded.task_mutations)
        self.assertEqual([], unverifiable.task_mutations)

    def test_parent_not_landed_is_refused_without_task_mutation(self):
        """Parent tooling is direct-to-main, so no unlanded close is legitimate."""
        runner = FakeRunner(code_state="not-landed")

        rc = verified_close.main(
            [
                "parent-task",
                "--code",
                "b" * 40,
                "--repo",
                verified_close.PARENT_REPO,
                "--source",
                ".",
            ],
            run=runner,
        )

        self.assertEqual(verified_close.REFUSED, rc)
        self.assertEqual([], runner.task_mutations)

    def test_unlanded_closure_records_the_landing_state_in_the_note(self):
        """`closed` no longer implies landed, so the note must say which it was.

        Without this the closure record is indistinguishable from a landing
        claim -- the exact proxy-binding error the drain rule exists to avoid.
        """
        runner = FakeRunner(code_state="not-landed")
        self.assertEqual(
            verified_close.CLOSED,
            verified_close.main(
                ["fixture-task", "--code", "b" * 40, "--source", "."], run=runner
            ),
        )
        notes = [c for c in runner.task_mutations if c[:2] == ("tg", "note")]
        self.assertEqual(1, len(notes), notes)
        self.assertIn("landing=implemented-unlanded", notes[0][-1])
        self.assertIn("b" * 40, notes[0][-1])

        landed = FakeRunner(code_state="landed")
        verified_close.main(
            ["fixture-task", "--code", "a" * 40, "--source", "."], run=landed
        )
        landed_notes = [c for c in landed.task_mutations if c[:2] == ("tg", "note")]
        self.assertIn("landing=landed", landed_notes[0][-1])

    def test_unlanded_close_is_refused_when_the_implemented_tag_is_absent(self):
        """The negative bracket for the tag gate.

        `drain-implemented-to-landed` selects on the `implemented` tag. Closing
        an unlanded task without it would drop the work out of ready, active,
        AND the drain simultaneously -- invisible rather than pending. Plant the
        violating case (no tag) and confirm refusal with no task mutation.
        """
        runner = FakeRunner(code_state="not-landed", tags="infra, process")
        self.assertEqual(
            verified_close.REFUSED,
            verified_close.main(
                ["fixture-task", "--code", "b" * 40, "--source", "."], run=runner
            ),
        )
        self.assertEqual([], runner.task_mutations)

    def test_landed_close_does_not_require_the_implemented_tag(self):
        """Anti-vacuity guard for the gate above.

        A gate that refused every untagged close would also block the ordinary
        landed path, so assert the tag requirement is scoped to the unlanded
        state only.
        """
        runner = FakeRunner(code_state="landed", tags="infra, process")
        self.assertEqual(
            verified_close.CLOSED,
            verified_close.main(
                ["fixture-task", "--code", "a" * 40, "--source", "."], run=runner
            ),
        )
        self.assertNotEqual([], runner.task_mutations)

    def test_four_legitimate_fixture_closures_succeed(self):
        runner = FakeRunner()
        cases = (
            ["code-task", "--code", "123", "--repo", "rrnewton/hermit", "--source", "."],
            ["artifact-task", "--artifact", "AGENTS.md"],
            ["run-task", "--run-id", "987", "--repo", "rrnewton/hermit"],
            [
                "observation-task",
                "--observation-command",
                '["git","-C","hermit","rev-list","--left-right","--count","HEAD...origin/main"]',
                "--observation-output",
                "0\t0",
            ],
        )
        results = [verified_close.main(case, run=runner) for case in cases]

        self.assertEqual([0, 0, 0, 0], results)
        notes = [command for command in runner.task_mutations if command[1] == "note"]
        closes = [command for command in runner.task_mutations if command[1] == "update"]
        self.assertEqual(4, len(notes))
        self.assertEqual(4, len(closes))
        self.assertTrue(all("CLOSURE-VERIFIED:" in command[3] for command in notes))
        self.assertTrue(all(command[-2:] == ("--status", "closed") for command in closes))
        self.assertEqual(
            ["note", "update"] * 4,
            [command[1] for command in runner.task_mutations],
        )
        artifact_note = next(
            command[3]
            for command in notes
            if "kind=artifact" in command[3]
        )
        self.assertIn("rrnewton/dev-hermit:AGENTS.md@" + "d" * 40, artifact_note)
        self.assertIn("target=main@" + "c" * 40, artifact_note)
        observation_note = next(
            command[3]
            for command in notes
            if "kind=observation" in command[3]
        )
        git_path = str(Path(shutil.which("git") or "git").resolve())
        self.assertIn(
            f'reference=["{git_path}","-C","hermit","rev-list","--left-right","--count","HEAD...origin/main"]',
            observation_note,
        )
        self.assertIn(
            'resolved={"output":"0\\t0","returncode":0}',
            observation_note,
        )

    def test_fabricated_observation_output_is_refused_without_task_mutation(self):
        runner = FakeRunner(observation_output="0\t0\n")

        rc = verified_close.main(
            [
                "fabricated-observation",
                "--observation-command",
                '["git","-C","hermit","rev-list","--left-right","--count","HEAD...origin/main"]',
                "--observation-output",
                "1\t0",
            ],
            run=runner,
        )

        self.assertEqual(verified_close.REFUSED, rc)
        self.assertEqual([], runner.task_mutations)
        self.assertTrue(
            any(
                Path(command[0]).name == "git"
                and command[1:]
                == (
                    "-C",
                    "hermit",
                    "rev-list",
                    "--left-right",
                    "--count",
                    "HEAD...origin/main",
                )
                for command in runner.commands
            ),
            runner.commands,
        )

    def test_mutating_observation_command_is_refused_before_execution(self):
        runner = FakeRunner()

        rc = verified_close.main(
            [
                "not-a-bypass",
                "--observation-command",
                '["tg","update","not-a-bypass","--status","closed"]',
                "--observation-output",
                "closed",
            ],
            run=runner,
        )

        self.assertEqual(verified_close.REFUSED, rc)
        self.assertEqual([], runner.commands)
        self.assertEqual([], runner.task_mutations)

    def test_systemctl_option_value_cannot_hide_a_mutating_subcommand(self):
        runner = FakeRunner()

        rc = verified_close.main(
            [
                "systemctl-not-a-bypass",
                "--observation-command",
                '["systemctl","--root","/tmp","restart","example.service"]',
                "--observation-output",
                "restarted",
            ],
            run=runner,
        )

        self.assertEqual(verified_close.REFUSED, rc)
        self.assertEqual([], runner.commands)
        self.assertEqual([], runner.task_mutations)

    def test_caller_writable_lookalike_observer_is_refused(self):
        import tempfile

        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            lookalike = Path(directory) / "git"
            lookalike.write_text("#!/bin/sh\nprintf fabricated\\n")
            lookalike.chmod(0o755)

            rc = verified_close.main(
                [
                    "lookalike-not-a-bypass",
                    "--observation-command",
                    json.dumps([str(lookalike), "status"]),
                    "--observation-output",
                    "fabricated",
                ],
                run=runner,
            )

        self.assertEqual(verified_close.REFUSED, rc)
        self.assertEqual([], runner.commands)
        self.assertEqual([], runner.task_mutations)

    def test_observation_command_and_output_are_an_indivisible_pair(self):
        command_only = FakeRunner()
        output_smuggled_into_code = FakeRunner()

        self.assertEqual(
            verified_close.REFUSED,
            verified_close.main(
                [
                    "missing-output",
                    "--observation-command",
                    '["git","-C","hermit","rev-list","--left-right","--count","HEAD...origin/main"]',
                ],
                run=command_only,
            ),
        )
        self.assertEqual(
            verified_close.REFUSED,
            verified_close.main(
                [
                    "stray-output",
                    "--code",
                    "a" * 40,
                    "--observation-output",
                    "fabricated",
                ],
                run=output_smuggled_into_code,
            ),
        )
        self.assertEqual([], command_only.commands)
        self.assertEqual([], output_smuggled_into_code.commands)
        self.assertEqual([], command_only.task_mutations)
        self.assertEqual([], output_smuggled_into_code.task_mutations)

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


class ContentLostRefusesClosure(unittest.TestCase):
    """THE CONSUMER TEST. Detectable-in-principle is not the same as refused-in-practice.

    A detector nothing calls has zero coverage. These assert that verify_code -- the
    engine behind `ci-hub/bin/close-task` -- actually REFUSES a landing whose content
    did not survive, and still ACCEPTS one whose content did.
    """

    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "r"
        self.repo.mkdir()

        def g(*a: str) -> str:
            import subprocess as sp
            p = sp.run(["git", "-C", str(self.repo), *a], capture_output=True, text=True)
            if p.returncode:
                raise AssertionError(f"git {a}: {p.stderr}")
            return p.stdout.strip()

        self.g = g
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        body = ["top"] + [f"pad{i}" for i in range(20)] + ["bottom"]
        (self.repo / "f.py").write_text("\n".join(body) + "\n")
        g("add", "f.py")
        g("commit", "-qm", "base")
        self.base = g("rev-parse", "HEAD")
        b = (self.repo / "f.py").read_text().splitlines()
        b[0] = "top CHANGED-A"
        b[-1] = "bottom CHANGED-B"
        (self.repo / "f.py").write_text("\n".join(b) + "\n")
        g("add", "f.py")
        g("commit", "-qm", "feature")
        self.feature = g("rev-parse", "HEAD")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _landed_verifier(self):
        """Stub the upstream verifier so it reports LANDED, as ancestry would."""
        import json as _json
        import subprocess as _sp

        def run(command, cwd=None, **kw):
            payload = _json.dumps(
                {"state": "landed", "resolved_sha": self.feature, "reason": "landed"}
            )
            return _sp.CompletedProcess(list(command), 0, payload, "")

        return run

    def test_lossy_reconcile_is_REFUSED_by_the_closure_gateway(self) -> None:
        self.g("checkout", "-q", "-b", "lossy", self.base)
        self.g("merge", "-q", "--no-commit", "--no-ff", self.feature)
        b = (self.repo / "f.py").read_text().splitlines()
        b[-1] = "bottom"                      # drop one hunk, keep the SHA an ancestor
        (self.repo / "f.py").write_text("\n".join(b) + "\n")
        self.g("add", "f.py")
        self.g("commit", "-qm", "lossy reconcile")
        target = self.g("rev-parse", "HEAD")

        ev = verified_close.verify_code(
            "PR-1", repo="rrnewton/dev-hermit", source=self.repo,
            target=target, run=self._landed_verifier(),
        )
        self.assertEqual(ev.state, "refused", ev.reason)
        self.assertIn("CONTENT-LOST", ev.reason)
        self.assertIn("1 of 2 hunk", ev.reason)

    def test_intact_landing_still_CLOSES(self) -> None:
        """A gate that refuses everything is useless; the honest case must still pass."""
        self.g("checkout", "-q", "-b", "clean", self.base)
        self.g("merge", "-q", "--no-ff", "--no-edit", self.feature)
        target = self.g("rev-parse", "HEAD")
        ev = verified_close.verify_code(
            "PR-2", repo="rrnewton/dev-hermit", source=self.repo,
            target=target, run=self._landed_verifier(),
        )
        self.assertEqual(ev.state, "verified", ev.reason)
        self.assertEqual(ev.landing, "landed")

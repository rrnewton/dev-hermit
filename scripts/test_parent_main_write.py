#!/usr/bin/env python3
"""Both-direction integration tests for the parent-main writer authority."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parent.parent


def run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ, **(env or {}))
    return subprocess.run(args, cwd=cwd, env=merged, text=True, capture_output=True, check=False)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = run("git", *args, cwd=repo)
    if check and result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result


class ParentMainWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.remote = base / "origin.git"
        self.repo = base / "repo"
        self.lock = base / "parent-main.lock"
        git(base, "init", "--bare", "--initial-branch=main", str(self.remote))
        git(base, "init", "--initial-branch=main", str(self.repo))
        git(self.repo, "config", "user.name", "Test Writer")
        git(self.repo, "config", "user.email", "writer@example.invalid")
        (self.repo / "notes.md").write_text("seed\n")
        git(self.repo, "add", "notes.md")
        git(self.repo, "commit", "-m", "seed")
        git(self.repo, "remote", "add", "origin", str(self.remote))
        git(self.repo, "push", "-u", "origin", "main")

        (self.repo / "scripts").mkdir()
        shutil.copy2(SOURCE / "scripts/parent-main-write", self.repo / "scripts/parent-main-write")
        hooks = self.repo / ".githooks"
        hooks.mkdir()
        for name in ("pre-commit", "pre-push", "reference-transaction"):
            shutil.copy2(SOURCE / ".githooks" / name, hooks / name)
        git(self.repo, "config", "core.hooksPath", ".githooks")
        self.env = {
            "HERMIT_PARENT_MAIN_LOCK_PATH": str(self.lock),
            "HERMIT_PARENT_MAIN_NO_PROXY": "1",
        }
        self.seed = git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def writer(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run(str(self.repo / "scripts/parent-main-write"), *args, cwd=self.repo, env=self.env)

    def change(self, path: str = "notes.md", text: str = "changed\n") -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        git(self.repo, "add", path)

    def test_direct_main_commit_is_refused_before_ref_update(self) -> None:
        self.change()
        result = run("git", "commit", "-m", "bypass", "--", "notes.md", cwd=self.repo, env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing serialized-writer lock receipt", result.stderr)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").stdout.strip(), self.seed)

    def test_no_verify_cannot_bypass_reference_transaction(self) -> None:
        self.change()
        result = run(
            "git", "commit", "--no-verify", "-m", "bypass", "--", "notes.md",
            cwd=self.repo, env=self.env,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing serialized-writer lock receipt", result.stderr)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").stdout.strip(), self.seed)

    def test_ordinary_writer_commits_pushes_and_proves_ancestry(self) -> None:
        self.change()
        result = self.writer("commit", "-m", "update notes", "--", "notes.md")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ancestry=1/1 mode=ordinary", result.stdout)
        head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(head, git(self.repo, "rev-parse", "origin/main").stdout.strip())

    def test_stale_local_main_is_refused_before_commit(self) -> None:
        other = Path(self.temp.name) / "other"
        git(Path(self.temp.name), "clone", str(self.remote), str(other))
        git(other, "config", "user.name", "Other")
        git(other, "config", "user.email", "other@example.invalid")
        (other / "other.md").write_text("remote move\n")
        git(other, "add", "other.md")
        git(other, "commit", "-m", "remote move")
        git(other, "push", "origin", "main")
        self.change()

        result = self.writer("commit", "-m", "stale", "--", "notes.md")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local main is not the freshly fetched origin/main", result.stderr)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").stdout.strip(), self.seed)

    def test_second_writer_is_refused_by_host_mutex(self) -> None:
        with self.lock.open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run(
                str(self.repo / "scripts/parent-main-write"), "sync", cwd=self.repo,
                env=dict(self.env, HERMIT_PARENT_MAIN_LOCK_TIMEOUT="0"),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("another parent-main writer owns", result.stderr)

    def test_sensitive_commit_requires_and_retains_audit_receipt(self) -> None:
        self.change(".githooks/probe", "#!/bin/sh\n")
        refused = self.writer("commit", "-m", "hook repair", "--", ".githooks/probe")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("retained --audit-reason path", refused.stderr)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").stdout.strip(), self.seed)

        reason = "repair hook enforcement"
        allowed = self.writer(
            "commit", "-m", "hook repair", "--audit-reason", reason, "--", ".githooks/probe"
        )
        self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
        message = git(self.repo, "log", "-1", "--format=%B").stdout
        self.assertIn("Parent-Main-Write-Mode: audited", message)
        self.assertIn(f"Parent-Main-Write-Reason: {reason}", message)
        self.assertIn(f"Parent-Main-Write-Base: {self.seed}", message)

    def test_clean_slot_direct_push_is_refused_but_wrapper_publish_passes(self) -> None:
        git(self.repo, "switch", "-c", "feature")
        self.change("feature.md", "feature\n")
        git(self.repo, "commit", "-m", "feature", "--", "feature.md")
        direct = run("git", "push", "origin", "HEAD:refs/heads/main", cwd=self.repo, env=self.env)
        self.assertNotEqual(direct.returncode, 0)
        self.assertIn("missing serialized-writer lock receipt", direct.stderr)

        published = self.writer("publish")
        self.assertEqual(published.returncode, 0, published.stdout + published.stderr)
        self.assertIn("ancestry=1/1 mode=ordinary", published.stdout)

    def test_sensitive_feature_publish_without_commit_receipt_is_refused(self) -> None:
        git(self.repo, "switch", "-c", "feature")
        self.change(".githooks/probe", "#!/bin/sh\n")
        git(self.repo, "commit", "-m", "unaudited hook", "--", ".githooks/probe")
        result = self.writer("publish", "--audit-reason", "claimed after the fact")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lacks Parent-Main-Write-Mode trailer", result.stderr)


if __name__ == "__main__":
    unittest.main()

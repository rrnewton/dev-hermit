#!/usr/bin/env python3
"""Brackets for exact-byte append-only history repair."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-append-only-prefix.py")


class AppendOnlyPrefixTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="append-prefix-test.")
        self.repo = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.name", "Fixture"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email", "fixture@example.com"],
            check=True,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def commit_base(self, files: dict[str, bytes]) -> str:
        for name, content in files.items():
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        subprocess.run(["git", "-C", str(self.repo), "add", *files], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-q", "-m", "base"],
            check=True,
        )
        return subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def run_tool(self, base: str, *paths: str, repair: bool = False) -> subprocess.CompletedProcess[str]:
        command = [str(SCRIPT), "--repo", str(self.repo), "--base", base]
        if repair:
            command.append("--repair")
        command.extend(paths)
        return subprocess.run(command, check=False, capture_output=True, text=True)

    def test_repair_preserves_base_bytes_then_unique_additions(self) -> None:
        json_base = b'{"id":"base-1"}\n{"id":"base-2"}\n'
        markdown_base = b"# Archive\n\nbase one\nbase two\n"
        base = self.commit_base({"history.jsonl": json_base, "ARCHIVED.md": markdown_base})
        (self.repo / "history.jsonl").write_bytes(
            b'{"id":"base-1"}\n{"id":"new"}\n{"id":"base-2"}\n{"id":"new"}\n'
        )
        (self.repo / "ARCHIVED.md").write_bytes(
            b"# Archive\n\nbase one\n\n## New\nrecord\nbase two\n"
        )

        before = self.run_tool(base, "history.jsonl", "ARCHIVED.md")
        self.assertNotEqual(before.returncode, 0)
        repaired = self.run_tool(base, "history.jsonl", "ARCHIVED.md", repair=True)
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        self.assertEqual(
            (self.repo / "history.jsonl").read_bytes(),
            json_base + b'{"id":"new"}\n',
        )
        self.assertTrue((self.repo / "ARCHIVED.md").read_bytes().startswith(markdown_base))

    def test_non_additive_candidate_is_refused_without_rewrite(self) -> None:
        base_bytes = b"one\ntwo\n"
        base = self.commit_base({"ARCHIVED.md": base_bytes})
        target = self.repo / "ARCHIVED.md"
        target.write_bytes(b"one\nchanged\n")

        result = self.run_tool(base, "ARCHIVED.md", repair=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not additive", result.stderr)
        self.assertEqual(target.read_bytes(), b"one\nchanged\n")

    def test_mutable_base_name_is_refused(self) -> None:
        self.commit_base({"ARCHIVED.md": b"one\n"})

        result = self.run_tool("HEAD", "ARCHIVED.md")

        self.assertEqual(result.returncode, 2)
        self.assertIn("immutable lowercase 40-hex", result.stderr)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for the registered validate-worktree reader."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "worktrees.py"
SPEC = importlib.util.spec_from_file_location("validate_worktrees", MODULE_PATH)
assert SPEC and SPEC.loader
worktrees = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worktrees)


class WorktreeReportTests(unittest.TestCase):
    def test_default_store_uses_parent_ignored_convention(self) -> None:
        expected = MODULE_PATH.parents[2] / "ignored" / "ci-hub"
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CI_HUB_IGNORED_DIR", None)
            self.assertEqual(Path(worktrees.default_data_dir()), expected)

    def test_table_keeps_full_identity_and_explicit_units(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary)
            registry = {
                "/worktrees/ci/hermit": {
                    "path": "/worktrees/ci/hermit",
                    "slot": "ci",
                    "branch": "codex/validate-smart-selection",
                    "state": "timeout",
                    "last_result": "timeout",
                    "last_profile": "portable-strict-compat-only",
                    "last_selection_mode": "full",
                    "tree_dirty": True,
                    "commit_anchored": False,
                    "last_seen_epoch": 1,
                    "last_commit": "a" * 40,
                }
            }
            (store / "worktree-registry.json").write_text(json.dumps(registry))
            (store / "validate-runs.jsonl").write_text(
                json.dumps(
                    {
                        "finished_at": "2026-08-03T00:00:00Z",
                        "slot": "ci",
                        "profile": "portable-strict-compat-only",
                        "selection_mode": "full",
                        "result": "timeout",
                        "real_seconds": 12,
                        "commit": "a" * 40,
                    }
                )
                + "\n"
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch(
                "time.time", return_value=2
            ):
                self.assertEqual(
                    worktrees.main(["--data-dir", str(store), "--runs", "1"]), 0
                )
            rendered = output.getvalue()
            self.assertIn("portable-strict-compat-only", rendered)
            self.assertIn("timeout", rendered)
            self.assertIn("dirty", rendered)
            self.assertIn("WALL(s)", rendered)

    def test_missing_store_is_explicit_not_silent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    worktrees.main(["--data-dir", str(Path(temporary) / "absent")]),
                    0,
                )
            self.assertIn("no worktrees registered yet", output.getvalue())


if __name__ == "__main__":
    unittest.main()

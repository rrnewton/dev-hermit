#!/usr/bin/env python3
"""Unit tests for pr_conflict_graph.py; no network access required."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from pr_conflict_graph import (
    ConflictEdge,
    OrderingEdge,
    PrInfo,
    build_ordering_edges,
    build_stacks,
    merge_conflict_paths,
    plan_landing,
    select_prs,
)


def pr(
    number: int,
    *,
    head_ref: str | None = None,
    base_ref: str = "main",
    size: int = 10,
) -> PrInfo:
    return PrInfo(
        number=number,
        title=f"PR {number}",
        author="test",
        head_ref=head_ref or f"feature-{number}",
        base_ref=base_ref,
        api_head_sha=f"sha-{number}",
        is_draft=False,
        mergeable="MERGEABLE",
        review_decision="",
        created_at=f"2026-01-{number:02d}T00:00:00Z",
        updated_at=f"2026-01-{number:02d}T00:00:00Z",
        additions=size,
        deletions=0,
        api_changed_files=1,
        head_sha=f"sha-{number}",
        base_sha="base",
        files=frozenset({f"file-{number}"}),
    )


def raw_pr(number: int, head: str, base: str) -> dict[str, object]:
    return {
        "number": number,
        "title": f"PR {number}",
        "author": {"login": "test"},
        "baseRefName": base,
        "headRefName": head,
        "headRefOid": f"sha-{number}",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "reviewDecision": "",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "additions": 1,
        "deletions": 0,
        "changedFiles": 1,
    }


class ScopeTests(unittest.TestCase):
    def test_base_scope_includes_transitive_stacks(self) -> None:
        raw = [
            raw_pr(1, "parent", "main"),
            raw_pr(2, "child", "parent"),
            raw_pr(3, "grandchild", "child"),
            raw_pr(4, "unrelated", "release"),
        ]
        selected = select_prs(raw, "main", None)  # type: ignore[arg-type]
        self.assertEqual([item["number"] for item in selected], [1, 2, 3])


class MergeTreeTests(unittest.TestCase):
    def git(self, repo: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit(self, repo: Path, message: str) -> str:
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-q", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

    def test_detects_real_conflict_and_clean_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            self.git(repo, "init", "-q", "-b", "main")
            self.git(repo, "config", "user.email", "test@example.com")
            self.git(repo, "config", "user.name", "Test")
            (repo / "shared.txt").write_text("base\n")
            base = self.commit(repo, "base")

            self.git(repo, "switch", "-q", "-c", "left")
            (repo / "shared.txt").write_text("left\n")
            left = self.commit(repo, "left")

            self.git(repo, "switch", "-q", "--detach", base)
            self.git(repo, "switch", "-q", "-c", "right")
            (repo / "shared.txt").write_text("right\n")
            right = self.commit(repo, "right")

            self.git(repo, "switch", "-q", "--detach", base)
            self.git(repo, "switch", "-q", "-c", "clean")
            (repo / "clean.txt").write_text("clean\n")
            clean = self.commit(repo, "clean")

            self.assertEqual(
                merge_conflict_paths(str(repo), left, right), ("shared.txt",)
            )
            self.assertEqual(merge_conflict_paths(str(repo), left, clean), ())


class GraphTests(unittest.TestCase):
    def test_explicit_stack_and_paths(self) -> None:
        nodes = [
            pr(1, head_ref="parent"),
            pr(2, head_ref="child", base_ref="parent"),
            pr(3, base_ref="child"),
        ]
        edges = build_ordering_edges(nodes, git_dir=None)
        self.assertEqual(
            [(edge.before, edge.after) for edge in edges], [(1, 2), (2, 3)]
        )
        self.assertEqual(build_stacks(edges), [[1, 2, 3]])

    def test_landing_batches_respect_dependencies_and_conflicts(self) -> None:
        nodes = [pr(1), pr(2), pr(3, base_ref="feature-1")]
        conflicts = [ConflictEdge(1, 2, ("shared.rs",))]
        ordering = [OrderingEdge(1, 3, "base-ref")]
        plan = plan_landing(nodes, conflicts, ordering)
        batch_of = {
            number: index
            for index, batch in enumerate(plan.batches)
            for number in batch
        }
        self.assertNotEqual(batch_of[1], batch_of[2])
        self.assertLess(batch_of[1], batch_of[3])
        steps = {step.pr: step for step in plan.rebase_steps}
        self.assertIn(2, steps)
        self.assertIn(1, steps[2].after)
        self.assertIn(3, steps)
        self.assertIn(1, steps[3].after)

    def test_conflict_free_prs_share_a_batch(self) -> None:
        plan = plan_landing([pr(1), pr(2), pr(3)], [], [])
        self.assertEqual(plan.batches, [[1, 2, 3]])
        self.assertEqual(plan.rebase_steps, [])


if __name__ == "__main__":
    unittest.main()

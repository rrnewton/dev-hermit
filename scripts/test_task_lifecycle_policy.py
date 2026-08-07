#!/usr/bin/env python3
"""Reject repository instructions that tell implementation agents to self-close."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".md", ".py", ".rs", ".sh", ".ts", ".txt", ".yaml", ".yml"}
SCAN_ROOTS = (
    ROOT / "AGENTS.md",
    ROOT / ".claude" / "skills",
    ROOT / ".orc" / "plugins" / "hermit-dev",
    ROOT / "scripts",
    ROOT / "ci-hub",
    ROOT / "agent-utils",
)
FORBIDDEN = (
    re.compile(r"close\s+the\s+task\s+yourself", re.IGNORECASE),
    re.compile(r"post\s+a\s+summary\s+note\s+and\s+close", re.IGNORECASE),
    re.compile(
        r"when\s+done.{0,400}(?:tg\s+update[^\n]*--status\s+(?:closed|resolved)|"
        r"close\s+the\s+task)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"then:\s*`?tg\s+update[^\n]*--status\s+(?:closed|resolved)",
        re.IGNORECASE,
    ),
)


def iter_text_files(roots: tuple[Path, ...]):
    this_file = Path(__file__).resolve()
    for root in roots:
        if not root.exists():
            continue
        candidates = (root,) if root.is_file() else root.rglob("*")
        for candidate in candidates:
            if (
                candidate.is_file()
                and candidate.resolve() != this_file
                and candidate.suffix in TEXT_SUFFIXES
                and not {".git", "target", "node_modules", "__pycache__"}
                & set(candidate.parts)
            ):
                yield candidate


def find_forbidden(roots: tuple[Path, ...]) -> list[str]:
    findings: list[str] = []
    for path in iter_text_files(roots):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in FORBIDDEN:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{path}:{line}: {match.group(0)!r}")
    return findings


class TaskLifecyclePolicyTest(unittest.TestCase):
    def test_agent_facing_text_has_no_self_close_instruction(self) -> None:
        self.assertEqual([], find_forbidden(SCAN_ROOTS))

    def test_planted_self_close_instruction_reaches_matcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "agent-template.md"
            fixture.write_text(
                "When done: post evidence.\n"
                "Then: tg update example --status " "closed\n",
                encoding="utf-8",
            )
            findings = find_forbidden((fixture,))

        self.assertEqual(2, len(findings))
        self.assertTrue(all("agent-template.md" in finding for finding in findings))
        self.assertTrue(any("When done" in finding for finding in findings))
        self.assertTrue(any("Then:" in finding for finding in findings))

    def test_canonical_policy_explains_the_agent_stop_condition(self) -> None:
        policy = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for required in (
            "A working agent NEVER moves a task to a terminal status",
            "add the `implemented` tag while leaving status `in_progress`",
            "(4) stop",
            "Only the coordinator closes tasks",
        ):
            self.assertIn(required, policy)

        rationale = (ROOT / "ai_docs" / "agents-md-policy-rationale.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "Phantom closures (a task marked done while its work never landed)",
            rationale,
        )

        coordinator_skill = " ".join(
            (ROOT / ".claude" / "skills" / "hermit-coord" / "SKILL.md")
            .read_text(encoding="utf-8")
            .split()
        )
        self.assertIn(
            "Closing earlier hides unlanded work from the active drain",
            coordinator_skill,
        )


if __name__ == "__main__":
    unittest.main()

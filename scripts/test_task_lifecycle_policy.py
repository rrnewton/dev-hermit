#!/usr/bin/env python3
"""Keep repository instructions agreeing that an agent closes its own task.

INVERTED 2026-08-08. This file used to assert the opposite: it forbade any text
telling an agent to self-close and REQUIRED the coordinator-only closure
gateway. That policy was unsatisfiable -- the ORC coordinator has no shell and
so could never invoke `./ci-hub/bin/close-task` -- while the generic ORC
dispatch preamble told every agent to self-close on every dispatch. This gate
was therefore actively enforcing the contradiction, and would reject the stock
ORC preamble as a violation.

The evidence requirement did NOT go away with the gatekeeper, so this file now
guards the half that is easy to lose: closing is still gated on a recorded
IMPLEMENTED note, the `implemented` tag still carries landing debt, and
`./ci-hub/bin/close-task` is still the only writer of the `CLOSURE-VERIFIED`
note that discharges it.
"""

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

# Reinstating any of these would restore the jam: a rule only the coordinator
# can satisfy, addressed to a coordinator that cannot run commands.
FORBIDDEN = (
    re.compile(r"only\s+the\s+coordinator\s+closes", re.IGNORECASE),
    re.compile(
        r"a\s+working\s+agent\s+NEVER\s+moves\s+a\s+task\s+to\s+a\s+terminal\s+status",
        re.IGNORECASE,
    ),
    re.compile(
        r"never\s+use\s+raw\s+`?tg\s+update[^\n]*--status\s+(?:closed|resolved)",
        re.IGNORECASE,
    ),
    re.compile(r"leaves?\s+status\s+`?in_progress`?,?\s+and\s+stops?", re.IGNORECASE),
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
    def test_no_coordinator_only_closure_rule_survives(self) -> None:
        self.assertEqual([], find_forbidden(SCAN_ROOTS))

    def test_planted_coordinator_only_rule_reaches_matcher(self) -> None:
        """NEGATIVE bracket: the matcher is not inert."""
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "stale-policy.md"
            fixture.write_text(
                "Only the coordinator closes tasks, and only through the gateway.\n"
                "Never use raw `tg update <id> --status closed`.\n",
                encoding="utf-8",
            )
            findings = find_forbidden((fixture,))

        self.assertEqual(2, len(findings))
        self.assertTrue(all("stale-policy.md" in finding for finding in findings))

    def test_current_self_close_wording_is_not_flagged(self) -> None:
        """POSITIVE bracket: the wording the policy now MANDATES must pass.

        Without this, tightening a pattern above could silently start rejecting
        the correct instruction again -- which is exactly the failure this file
        was rewritten to end.
        """
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "good-policy.md"
            fixture.write_text(
                "Post the evidence, then close the task yourself when done: "
                "`tg update <id> --status closed`.\n",
                encoding="utf-8",
            )
            self.assertEqual([], find_forbidden((fixture,)))

    def test_canonical_policy_states_the_agent_closes_its_own_task(self) -> None:
        policy = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for required in (
            # The rule itself.
            "the owning agent closes its own task",
            "tg update <id> --status closed",
            # The evidence half, which the gatekeeper's removal must NOT take
            # with it.
            "Record the evidence BEFORE you change status",
            'tg note <id> "IMPLEMENTED:',
            "tg update <id> --tags <existing-tags>,implemented",
            # Landing debt still has an owner and a discharge mechanism.
            "Landing debt rides on the `implemented`\ntag, never on the status",
            "CLOSURE-VERIFIED",
            "./ci-hub/bin/close-task",
        ):
            self.assertIn(required, policy)

    def test_coordinator_skill_agrees_with_the_canonical_policy(self) -> None:
        coordinator_skill = " ".join(
            (ROOT / ".claude" / "skills" / "hermit-coord" / "SKILL.md")
            .read_text(encoding="utf-8")
            .split()
        )
        self.assertIn("CLOSES ITS OWN TASK", coordinator_skill)
        self.assertIn(
            "Task closure is NOT a coordinator duty", coordinator_skill
        )


if __name__ == "__main__":
    unittest.main()

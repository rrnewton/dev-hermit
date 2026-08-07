#!/usr/bin/env python3
"""Validate that a guard PR's review comment proves the guard by deletion.

Policy: `.claude/skills/post-facto-review.md` section 3. Reading a guard
confirms it exists; deleting it confirms it is load-bearing. A review of a PR
whose value is a guard must record three things, and this checks for all three:

    DELETION      which guard was removed or neutralised
    REPRODUCTION  the failure that returned, with its observed symptom
    RESTORATION   the guard put back, suite green again

Two outcomes are valid. ``PROVEN`` means the failure returned when the guard was
removed. ``INERT`` means it did not — which is a reportable finding, not an
approval, and the checker treats a comment claiming approval while describing an
inert guard as a contradiction rather than a pass.

The failure mode this exists to catch is a review that reads the guard, says it
looks correct, and approves. That review cannot distinguish a working guard from
a no-op, because both read correctly and both leave the suite green.

Usage::

    guard_deletion_evidence.py --comment review.md
    guard_deletion_evidence.py --comment review.md --require-proven

Exit codes: 0 evidence complete, 1 incomplete or contradictory, 2 unreadable.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DELETION = "deletion"
REPRODUCTION = "reproduction"
RESTORATION = "restoration"
ELEMENTS = (DELETION, REPRODUCTION, RESTORATION)

#: Each element is evidenced by any of its patterns. Deliberately generous about
#: wording and strict about the element being present at all -- the point is to
#: catch a review that never removed anything, not to police vocabulary.
PATTERNS = {
    DELETION: (
        r"\bdelet(?:e|ed|ing|ion)\b",
        r"\bremov(?:e|ed|ing|al)\b",
        r"\bneutralis(?:e|ed)\b|\bneutraliz(?:e|ed)\b",
        r"\bcommented out\b",
        r"\bmutat(?:e|ed|ion)\b",
    ),
    REPRODUCTION: (
        r"\breproduc(?:e|ed|es|tion)\b",
        r"\bfailure returned\b",
        r"\bwent red\b",
        r"\bthe symptom\b",
        r"\bobserved\b.*\b(fail|error|diverge|panic|exit)\w*\b",
    ),
    RESTORATION: (
        r"\brestor(?:e|ed|ation)\b",
        r"\bput back\b",
        r"\breinstat(?:e|ed)\b",
        r"\bre-?applied\b",
        r"\bgreen again\b",
    ),
}

INERT_PATTERNS = (
    r"\bINERT\b",
    r"\bdeletion changed nothing\b",
    r"\bno failure returned\b",
    r"\bstill (?:passed|green)\b",
)

APPROVAL_PATTERNS = (
    r"\bpassed-review\b",
    r"\bapprove(?:d|s)?\b",
    r"\bLGTM\b",
)

#: A mutation that never applied is not a detection. Reviews sometimes report a
#: green from an edit that silently failed to change the file.
NOOP_PATTERNS = (
    r"\bmutation did not apply\b",
    r"\bno-?op edit\b",
    r"\bfailed to apply\b",
)


@dataclass
class Verdict:
    present: tuple[str, ...]
    missing: tuple[str, ...]
    inert: bool
    approval_claimed: bool
    noop: bool

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def contradictory(self) -> bool:
        """Approval claimed while the guard is described as inert, or while the
        mutation never applied. Either way the green means nothing."""
        return self.approval_claimed and (self.inert or self.noop)

    @property
    def ok(self) -> bool:
        return self.complete and not self.contradictory

    def render(self) -> str:
        lines = [
            "guard-deletion evidence",
            f"  elements present : {len(self.present)} of {len(ELEMENTS)} "
            f"({', '.join(self.present) if self.present else 'none'})",
        ]
        if self.missing:
            lines.append(f"  MISSING          : {', '.join(self.missing)}")
        # "PROVEN" must never appear beside incomplete evidence -- that is the
        # very shape this checker exists to reject.
        if self.inert:
            outcome = "INERT"
        elif self.complete:
            outcome = "PROVEN"
        else:
            outcome = "NOT-ESTABLISHED"
        lines.append(f"  outcome          : {outcome}")
        if self.noop:
            lines.append("  WARNING          : the mutation is reported as not applied; "
                         "a no-op edit and a working guard produce the same green")
        if self.contradictory:
            lines.append("  CONTRADICTION    : approval claimed for a guard that is "
                         "inert or never actually removed")
        lines.append(f"  verdict          : {'OK' if self.ok else 'INCOMPLETE'}")
        return "\n".join(lines)


def _any(text: str, patterns) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def evaluate(comment: str) -> Verdict:
    if not comment.strip():
        raise ValueError("empty review comment; there is nothing to evidence")
    present = tuple(e for e in ELEMENTS if _any(comment, PATTERNS[e]))
    return Verdict(
        present=present,
        missing=tuple(e for e in ELEMENTS if e not in present),
        inert=_any(comment, INERT_PATTERNS),
        approval_claimed=_any(comment, APPROVAL_PATTERNS),
        noop=_any(comment, NOOP_PATTERNS),
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--comment", required=True, type=Path)
    ap.add_argument("--require-proven", action="store_true",
                    help="also fail if the guard is reported INERT")
    args = ap.parse_args(argv)
    try:
        verdict = evaluate(args.comment.read_text())
    except (OSError, ValueError) as error:
        print(f"guard-deletion-evidence: REFUSED: {error}", file=sys.stderr)
        return 2
    print(verdict.render())
    if not verdict.ok:
        return 1
    if args.require_proven and verdict.inert:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

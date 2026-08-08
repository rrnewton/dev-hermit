#!/usr/bin/env python3
"""Refuse to coalesce a change whose PR was closed ON THE MERITS.

WHY THIS EXISTS
---------------
A coalesce wave selects constituents by "ready and conflict-free" (see
`agent-utils/skills/pr-landing-operations/SKILL.md`, "Choose a landing shape",
step 2). Neither property says anything about whether a change was DELIBERATELY
REFUSED. The constituent list is hand-written into the wave's body -- PR #1633
enumerated 23 numbers in prose -- and nothing re-checks that list against PR
state at merge time, so a closed PR folded into a staging branch lands silently
under the wave's single approval.

THE TRAP THIS GUARD IS BUILT AROUND: "closed" does not mean "refused". Measured
on rrnewton/hermit 2026-08-07 over all 128 closed-not-merged PRs, the CLOSED
state is dominated by successful outcomes -- 54 carry a named successor and are
safe to fold. Blocking every closed PR would be as useless as blocking none; it
would stall the normal post-coalesce cleanup, where constituents are closed a
few minutes AFTER the wave lands (#1633's 23 were all closed 05:31:45-05:33:03
against a 05:28:47 merge, each with "LANDED via coalesce batch-2 (PR #1633,
squash mergeCommit b7f9c713...)").

The discriminator is what the close says, not that it happened:

  SUPERSEDED  names a successor -- "#1913", "landed via #1633", "superseded by
              main 4b9202c2", "CLOSED WITH NAMED SUCCESSOR". The work survives
              somewhere identifiable, so folding it is at worst redundant.
  MERITS      says the change is not to be landed -- "Closing this
              duplicate/vacuous aggregate WITHOUT LANDING" (#1726, #1701),
              "Closing without landing after exact-head adversarial audit"
              (#1641), "Closing this unsafe aggregate without landing"
              (#1672/#1670/#1668). Re-landing it reverses an explicit decision.

FAIL CLOSED ON SILENCE. A close with no comment, or a comment naming no
successor, is UNKNOWN -- and UNKNOWN is refused, not allowed. 53 of the 128 are
in that state. The asymmetry is the whole argument: wrongly refusing costs one
human sentence naming the successor, while wrongly allowing re-lands work an
owner spent review effort rejecting, inside a batch whose single approval hides
it. This mirrors the task's own predicate: refuse any change whose PR was closed
WITHOUT A NAMED SUCCESSOR.

Note what this guard does NOT claim: it does not verify that the named successor
actually landed. It proves "an owner pointed somewhere else", not "the work is
on main" -- that second claim belongs to ancestry verification, and conflating
them would be the same proxy-binding error this codebase keeps hitting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Disposition(Enum):
    """Why a candidate may or may not be folded into a coalesce wave."""

    ALLOW_OPEN = "allow: open PR, ordinary candidate"
    ALLOW_MERGED = "allow: already merged"
    ALLOW_SUPERSEDED = "allow: closed with a named successor"
    REFUSE_MERITS = "refuse: closed on the merits"
    REFUSE_UNKNOWN = "refuse: closed with no named successor"


#: An identifiable successor: a PR reference or a commit-ish (>=7 hex).
_SUCCESSOR = re.compile(r"#\d{2,6}|\b[0-9a-f]{7,40}\b")

#: Language that states the change is not to be landed. "without landing" is the
#: house phrase and carries most of the weight; the rest are observed variants.
_MERITS = re.compile(
    r"without landing"
    r"|not landing|do not land|won'?t land|will not land"
    r"|clos\w*\s+(?:this\s+)?\w*\s*(?:as\s+)?reject"
    r"|reject(?:ed|ing)?\s+on\s+the\s+merits"
    r"|declin(?:e|ed|ing)",
    re.I,
)

#: Language that points the work somewhere else.
_SUPERSEDED = re.compile(
    r"supersed\w*|landed via|folded into|replaced by|already landed"
    r"|named successor|duplicate of|successor",
    re.I,
)


@dataclass(frozen=True)
class Candidate:
    """A prospective coalesce constituent, as the wave author listed it."""

    number: int
    state: str  # OPEN | CLOSED | MERGED
    merged: bool = False
    close_comment: str = ""


@dataclass(frozen=True)
class Verdict:
    number: int
    disposition: Disposition
    reason: str

    @property
    def allowed(self) -> bool:
        return self.disposition.name.startswith("ALLOW")


def classify(candidate: Candidate) -> Verdict:
    """Decide whether one candidate may be folded into a wave.

    Pure and injectable so the policy is testable without a network or a live
    PR -- a guard whose own logic is untested can pass for the wrong reason.
    """
    state = (candidate.state or "").strip().upper()
    if candidate.merged or state == "MERGED":
        return Verdict(candidate.number, Disposition.ALLOW_MERGED, "already merged")
    if state == "OPEN":
        return Verdict(candidate.number, Disposition.ALLOW_OPEN, "open")

    body = candidate.close_comment or ""
    merits = _MERITS.search(body)
    if merits:
        # MERITS OUTRANKS A SUCCESSOR MENTION. "Closing this duplicate/vacuous
        # aggregate without landing" also matches "duplicate", and a refusal
        # that a stray word can downgrade to ALLOW is not a refusal.
        return Verdict(
            candidate.number,
            Disposition.REFUSE_MERITS,
            f"closed on the merits: {merits.group(0)!r}",
        )
    if _SUPERSEDED.search(body) and _SUCCESSOR.search(body):
        return Verdict(
            candidate.number,
            Disposition.ALLOW_SUPERSEDED,
            f"named successor {_SUCCESSOR.search(body).group(0)}",
        )
    return Verdict(
        candidate.number,
        Disposition.REFUSE_UNKNOWN,
        "closed with no named successor; fail closed",
    )


def screen(candidates):
    """Split a proposed constituent list into (allowed, refused) verdicts."""
    verdicts = [classify(c) for c in candidates]
    return (
        [v for v in verdicts if v.allowed],
        [v for v in verdicts if not v.allowed],
    )

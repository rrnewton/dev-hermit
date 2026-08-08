#!/usr/bin/env python3
"""Mutation tests for the coalesce closed-PR guard.

Every fixture below is a REAL close comment from rrnewton/hermit, quoted from
the 128 closed-not-merged PRs classified on 2026-08-07, because a guard proven
only against invented strings proves only that it matches invented strings.

Both directions are asserted. A guard that refuses everything is exactly as
useless as one that refuses nothing, and refusing everything is the easy
failure here: 128 of these PRs are closed, and 54 of them are safe to fold.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coalesce_guard import Candidate, Disposition, classify, screen  # noqa: E402


def closed(number: int, comment: str) -> Candidate:
    return Candidate(number=number, state="CLOSED", close_comment=comment)


# --- REAL superseded closes: the work survives at a named place -------------
SUPERSEDED = [
    closed(1622, "[coordinator, opus-4.8] LANDED via coalesce batch-2 (PR #1633, "
                 "squash mergeCommit b7f9c7131bc268a604c68edfd8d7cd6c42663ca1)."),
    closed(1956, "[orc-coord-014] Closing as **SUPERSEDED**. Successor: **#1913**."),
    closed(1861, "SUPERSEDED — closing, no action needed. `4be8edcd2 Bump Reverie "
                 "pin to 038e9939` landed on main independently."),
    closed(1768, "## Superseded by main `4b9202c2` — closing."),
    closed(1748, "Closing as superseded by #1750 (same file, same lines)."),
    closed(1925, "CLOSED WITH NAMED SUCCESSOR — exact duplicate of #1913."),
]

# --- REAL merits closes: an owner decided this must not land ----------------
MERITS = [
    closed(1726, "[coordinator, gpt-5.6-sol] Closing this duplicate/vacuous aggregate "
                 "without landing. Exact head b5c03d623f425d."),
    closed(1701, "Closing this duplicate/vacuous combined fixture without landing. "
                 "Exact head 391c49e."),
    closed(1641, "Closing without landing after exact-head adversarial audit. This "
                 "harness can report a pass it did not earn."),
    closed(1672, "Closing this unsafe aggregate without landing. Exact head 51fae5e1."),
    closed(1594, "Closing this >48-hour aggregate without landing. Exact head a155d3a8."),
]


class GuardBothDirectionsTest(unittest.TestCase):
    def test_merits_closes_are_refused(self) -> None:
        for c in MERITS:
            with self.subTest(pr=c.number):
                v = classify(c)
                self.assertFalse(v.allowed, f"#{c.number} must not be folded")
                self.assertIs(v.disposition, Disposition.REFUSE_MERITS)

    def test_superseded_closes_are_allowed(self) -> None:
        # The direction that makes the guard usable rather than merely safe.
        for c in SUPERSEDED:
            with self.subTest(pr=c.number):
                v = classify(c)
                self.assertTrue(v.allowed, f"#{c.number} is safe to fold")
                self.assertIs(v.disposition, Disposition.ALLOW_SUPERSEDED)

    def test_the_guard_is_not_a_blanket_refusal(self) -> None:
        allowed, refused = screen(SUPERSEDED + MERITS)
        self.assertEqual(len(allowed), len(SUPERSEDED))
        self.assertEqual(len(refused), len(MERITS))

    def test_merits_language_outranks_an_incidental_successor_mention(self) -> None:
        """#1726 says BOTH "duplicate" and "without landing".

        If a stray supersede-ish word could downgrade a refusal to ALLOW, the
        guard would pass exactly the PRs most likely to be re-landed, since a
        merits close usually cites the thing it duplicates.
        """
        v = classify(closed(1726, "Closing this duplicate/vacuous aggregate without "
                                  "landing. Exact head b5c03d6. See also #1700."))
        self.assertIs(v.disposition, Disposition.REFUSE_MERITS)

    def test_silence_fails_closed(self) -> None:
        for comment in ("", "Closing.", "no longer needed", "cleaning up the queue"):
            with self.subTest(comment=comment):
                v = classify(closed(999, comment))
                self.assertIs(v.disposition, Disposition.REFUSE_UNKNOWN)

    def test_supersede_word_without_an_identifier_is_not_enough(self) -> None:
        # "superseded" with nothing to point at names no survivor, so the work
        # cannot be shown to exist anywhere: refuse.
        v = classify(closed(998, "Closing as superseded."))
        self.assertIs(v.disposition, Disposition.REFUSE_UNKNOWN)

    def test_open_and_merged_candidates_are_untouched(self) -> None:
        self.assertIs(
            classify(Candidate(1, "OPEN")).disposition, Disposition.ALLOW_OPEN
        )
        self.assertIs(
            classify(Candidate(2, "MERGED", merged=True)).disposition,
            Disposition.ALLOW_MERGED,
        )

    def test_the_1633_constituents_would_still_have_been_allowed(self) -> None:
        """Regression on the motivating wave.

        #1633's 23 constituents were closed 3-5 minutes AFTER it merged, each
        with "LANDED via coalesce batch-2". A guard that blocked those would
        break the normal post-coalesce cleanup while fixing nothing.
        """
        v = classify(SUPERSEDED[0])
        self.assertTrue(v.allowed)
        # The successor it names is the wave itself, #1633 -- the PR reference,
        # not the squash SHA that also appears in the comment.
        self.assertIn("#1633", v.reason)


if __name__ == "__main__":
    unittest.main()

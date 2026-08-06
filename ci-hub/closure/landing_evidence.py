#!/usr/bin/env python3
"""WHAT COUNTS AS LANDING EVIDENCE — one predicate, so the rule is appealable.

THE RULE
--------
Landing is a fact about `main`. It is established by ANCESTRY on a freshly
fetched target, or by the directives ledger which itself checks that ancestry.
Nothing else is landing evidence -- and in particular:

  **tg CANNOT VERIFY LANDING. It is a TRACKER, not a source of truth on main.**

A `tg` task reads `closed` because a coordinator closed it; the close is a
RECORD of a verification, never the verification. Reading it back as proof is
circular: the tracker would be certifying the thing it was told.

WHY THIS FILE EXISTS WHEN THE ENFORCEMENT ALREADY DOES
-------------------------------------------------------
Two mechanisms already enforce this at the points they own:

  * `ci-hub/closure/verified_close.py` (the `close-task` gateway) FETCHES the
    remote and then tests `mergeCommit.oid` ancestry, returning
    CLOSED / REFUSED / UNVERIFIABLE. A close cannot happen without that.
  * `ci-hub/directives/` states the rule in its README -- "a quotation,
    dispatch, design document, branch, or open pull request is not completion"
    -- and `check.py` reports `satisfied` only on freshly-fetched ancestry.

Neither is reusable by a THIRD party. An agent asking "does this thing prove it
landed?" has no predicate to call, so the rule survives only as prose that must
be remembered -- and a rule that must be remembered is one that decays. A grep
of ci-hub finds no code consumer treating tg state as landing evidence today;
the exposure is agent behaviour, which is exactly what an appealable predicate
addresses.

THE NON-AUTHORITATIVE LIST IS THE USEFUL PART. Each entry below is a thing that
has actually been mistaken for landing evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import sys
from typing import Any, Optional

AUTHORITATIVE = "AUTHORITATIVE"
NON_AUTHORITATIVE = "NON-AUTHORITATIVE"
UNVERIFIABLE = "UNVERIFIABLE"

# Every one of these has been read as "it landed" at some point. The reason
# string is the part a reader needs: not "no", but "no, and here is the thing
# it actually tells you".
NON_AUTHORITATIVE_SOURCES: dict[str, str] = {
    "tg-status-closed": (
        "tg is a TRACKER, not a source of truth on main. `closed` records that a "
        "coordinator closed the task; reading it back as proof is circular"),
    "tg-implemented-tag": (
        "`implemented` explicitly means published-but-NOT-landed -- it is the tag "
        "that exists precisely to distinguish the two"),
    "tg-note-claiming-landed": (
        "a note is one agent's unverified belief; 'X appears to be landed' is not "
        "'X is landed'"),
    "pr-merged-flag": (
        "MERGED alone is insufficient: a later force-push ORPHANS the replay SHA "
        "(~12 PRs on 2026-08-03 were flagged MERGED with the commit unreachable)"),
    "pr-head-ancestry": (
        "the PR HEAD is NEVER an ancestor after a rebase replay -- this form read "
        "79 unlanded when 46 had landed. Ancestry must be tested on mergeCommit.oid"),
    "locally-validated-label": (
        "a label is a CACHE of a fact, not the fact; it was live on four PRs with "
        "no backing ledger record"),
    "green-check": (
        "a green check says CI passed on some commit, not that the commit reached "
        "main"),
    "branch-exists-on-remote": (
        "a pushed branch is a proposal; it says nothing about the target"),
    "merge-queue-position": (
        "being queued is the opposite of having landed"),
}


@dataclass
class EvidenceVerdict:
    kind: str
    authority: str
    reason: str
    detail: dict = field(default_factory=dict)

    @property
    def proves_landing(self) -> bool:
        return self.authority == AUTHORITATIVE

    def as_dict(self) -> dict[str, Any]:
        d = {"kind": self.kind, "authority": self.authority, "reason": self.reason}
        if self.detail:
            d["detail"] = self.detail
        return d

    def render(self) -> str:
        return f"landing-evidence: {self.authority} ({self.kind}) — {self.reason}"


def classify_evidence(
    kind: str,
    *,
    fetched_fresh: Optional[bool] = None,
    merge_commit_oid: Optional[str] = None,
    is_ancestor: Optional[bool] = None,
    ledger_satisfied: Optional[bool] = None,
) -> EvidenceVerdict:
    """Is `kind` a thing that proves a change reached main?

    Unknown kinds are UNVERIFIABLE rather than allowed: a source nobody has
    classified is precisely the one to refuse, because the reader has no way to
    know which of the two lists it belongs to.
    """
    if kind in NON_AUTHORITATIVE_SOURCES:
        return EvidenceVerdict(kind, NON_AUTHORITATIVE, NON_AUTHORITATIVE_SOURCES[kind])

    if kind == "merge-commit-ancestry":
        if not fetched_fresh:
            return EvidenceVerdict(
                kind, UNVERIFIABLE,
                "the target was not freshly fetched -- a stale ref answers about "
                "the past, which is the one thing landing must not be read from")
        if not merge_commit_oid:
            return EvidenceVerdict(
                kind, UNVERIFIABLE,
                "no mergeCommit.oid supplied; there is nothing to test ancestry on")
        if is_ancestor is None:
            return EvidenceVerdict(kind, UNVERIFIABLE,
                                   "ancestry was not determined (commit absent locally?)",
                                   {"merge_commit": merge_commit_oid})
        if not is_ancestor:
            return EvidenceVerdict(
                kind, NON_AUTHORITATIVE,
                "mergeCommit.oid is NOT an ancestor of the freshly fetched target: "
                "orphaned by a force-push, or never landed",
                {"merge_commit": merge_commit_oid})
        return EvidenceVerdict(kind, AUTHORITATIVE,
                               "mergeCommit.oid is an ancestor of the freshly fetched target",
                               {"merge_commit": merge_commit_oid})

    if kind == "directives-ledger":
        if ledger_satisfied is None:
            return EvidenceVerdict(kind, UNVERIFIABLE, "the ledger was not consulted")
        if not ledger_satisfied:
            return EvidenceVerdict(kind, NON_AUTHORITATIVE,
                                   "the ledger does not report this obligation satisfied")
        # The ledger is authoritative because it performs the ancestry check
        # itself -- it is a cache WITH a dereference, not a label.
        return EvidenceVerdict(
            kind, AUTHORITATIVE,
            "the directives ledger reports satisfied, which it grants only on "
            "freshly-fetched mergeCommit.oid/SHA ancestry")

    return EvidenceVerdict(
        kind, UNVERIFIABLE,
        "unknown evidence kind -- an unclassified source is refused, because a "
        "reader cannot tell which list it belongs to")


def require_landing_evidence(verdicts: list[EvidenceVerdict]) -> EvidenceVerdict:
    """Accept iff at least one AUTHORITATIVE verdict is present.

    Deliberately not 'the best of' or a score: ten non-authoritative sources do
    not sum to one authoritative one. That is the arithmetic error this whole
    class of defect is made of.
    """
    for v in verdicts:
        if v.proves_landing:
            return v
    if not verdicts:
        return EvidenceVerdict("none", UNVERIFIABLE, "no evidence supplied at all")
    kinds = ", ".join(v.kind for v in verdicts)
    return EvidenceVerdict(
        "aggregate", NON_AUTHORITATIVE,
        f"{len(verdicts)} source(s) supplied ({kinds}) and NONE is authoritative; "
        "non-authoritative sources do not accumulate into proof")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", help="evidence kind to classify")
    ap.add_argument("--list", action="store_true",
                    help="list the sources that are NOT landing evidence")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.list or not args.kind:
        if args.json:
            print(json.dumps({"non_authoritative": NON_AUTHORITATIVE_SOURCES,
                              "authoritative": ["merge-commit-ancestry",
                                                "directives-ledger"]}, indent=2))
        else:
            print("AUTHORITATIVE landing evidence:")
            print("  merge-commit-ancestry  (mergeCommit.oid, freshly fetched target)")
            print("  directives-ledger      (satisfied; it performs that ancestry check)")
            print("\nNOT landing evidence:")
            for k, why in NON_AUTHORITATIVE_SOURCES.items():
                print(f"  {k}\n      {why}")
        return 0

    v = classify_evidence(args.kind)
    print(json.dumps(v.as_dict(), indent=2) if args.json else v.render())
    return 0 if v.proves_landing else 1


if __name__ == "__main__":
    raise SystemExit(main())

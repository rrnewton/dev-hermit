#!/usr/bin/env python3
"""SOFT-INHERITED VALIDATION ACROSS A CLEAN REBASE — corroboration + debt.

THE PROBLEM, which is arithmetic rather than a coverage gap
-----------------------------------------------------------
A validate record is keyed to a SHA, and every rebase changes the head. So in a
rebase-heavy drain the record is stale BY CONSTRUCTION at exactly the moment it
is needed: 18 of 20 ready hermit PRs had no exact-head record. Landing ~50
same-file PRs serially does not merely cost ~50 rebases, it INVALIDATES ~50
validate records. Serial draining is self-defeating.

THE DESIGN (owner)
------------------
  clean rebase        -> the new head SOFT-INHERITS the old head's validated status
  conflict resolution -> NO inheritance (the content changed)
  soft-green          -> a DISTINCT state, never a full green
  soft-green on main  -> carries a DEBT: upgrade post-facto to a full green, OR
                         a later full green on main redeems the branch

WHY THIS MODULE EXISTS ALONGSIDE THE WRAPPER
--------------------------------------------
`rebase_wrapper.py` already implements the driven path correctly: its `rebase`
subcommand RUNS the rebase and OBSERVES cleanliness (`returncode == 0`), and
refuses to soft-green when it sees conflicts. That path is sound.

The `record` subcommand is the hole. It accepts `--conflicts` as an
agent-supplied CLAIM defaulting to `"none"`, so an agent that resolved conflicts
out of band and then calls `record` gets soft-green(zero-conflict) on an
unverified assertion. The task names exactly this: *"'I rebased cleanly'
asserted by an agent is worthless -- otherwise soft-inherit becomes a way to
launder an unvalidated head into a validated one, which is precisely the
locally-validated-with-no-backing-run defect in a new costume."*

So: CORROBORATE THE CLAIM AGAINST THE ARTEFACT. `git patch-id` is what makes
"the branch's own patches are unchanged" checkable -- a rebase preserves patch
ids while changing every SHA, so comparing SHAs would report a total change and
comparing trees would miss an added commit. If the patch-id multiset before and
after differs, content changed, and no claim of cleanliness survives that.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import json
from typing import Any, Optional, Sequence

# Inheritance verdicts.
INHERIT_SOFT = "inherit-soft"
NO_INHERIT = "no-inherit"
REFUSED = "refused"

# How the cleanliness of a rebase came to be believed. Only OBSERVED and
# CORROBORATED may inherit; a bare CLAIMED may not, however confident it is.
OBSERVED = "observed"          # the tool ran the rebase and watched it succeed
CORROBORATED = "corroborated"  # after the fact, patch-ids prove content unchanged
CLAIMED = "claimed"            # an agent said so and nothing checked

# Debt states for a soft-green that reached main.
DEBT_OUTSTANDING = "outstanding"
DEBT_UPGRADED = "upgraded"     # a full green was later recorded AT THAT COMMIT
DEBT_REDEEMED = "redeemed"     # a later full green ON MAIN restored good standing


@dataclass
class RebaseEvidence:
    """Everything known about one rebase X -> Z onto base Y."""

    source: str                       # X, the pre-rebase head
    base: str                         # Y, the base rebased onto
    result: str                       # Z, the new head
    claimed_conflicts: Sequence[str] = ()
    observed_conflicts: Optional[Sequence[str]] = None   # None = not observed
    patch_ids_before: Optional[Sequence[str]] = None
    patch_ids_after: Optional[Sequence[str]] = None


@dataclass
class InheritVerdict:
    verdict: str
    basis: str                        # OBSERVED / CORROBORATED / CLAIMED
    reason: str
    detail: dict = field(default_factory=dict)

    @property
    def inherits(self) -> bool:
        return self.verdict == INHERIT_SOFT

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "basis": self.basis,
                "reason": self.reason, **({"detail": self.detail} if self.detail else {})}


def classify_rebase(ev: RebaseEvidence) -> InheritVerdict:
    """Decide whether Z may soft-inherit X's validated status.

    Order matters, and it is the point: EVIDENCE BEATS CLAIM. Patch-ids are
    consulted before the agent's `--conflicts` value, so a claim of "clean" that
    the artefact contradicts is refused rather than believed.
    """
    claimed_clean = not list(ev.claimed_conflicts)

    # 1. Direct observation (the wrapper's `rebase` path ran it and watched).
    if ev.observed_conflicts is not None:
        observed = list(ev.observed_conflicts)
        if observed:
            return InheritVerdict(
                NO_INHERIT, OBSERVED,
                "conflicts were resolved: the content changed, so the old head's "
                "evidence does not describe this one",
                {"conflicts": observed,
                 **({"claim_contradicted": "agent claimed clean"} if claimed_clean else {})},
            )
        # Observed clean. Patch-ids, when available, must not contradict it.
        if _patch_ids_differ(ev):
            return InheritVerdict(
                REFUSED, OBSERVED,
                "observed clean but the patch-ids CHANGED -- the observation and the "
                "artefact disagree; refusing rather than picking one",
                _patch_detail(ev))
        return InheritVerdict(INHERIT_SOFT, OBSERVED,
                              "rebase ran clean under observation; only the base moved")

    # 2. No observation: the claim must be corroborated by the artefact.
    if ev.patch_ids_before is None or ev.patch_ids_after is None:
        return InheritVerdict(
            NO_INHERIT, CLAIMED,
            "cleanliness was CLAIMED but never observed and cannot be corroborated "
            "(no patch-ids) -- an unverified claim is exactly how an unvalidated "
            "head gets laundered into a validated one",
            {"claimed_clean": claimed_clean})
    if _patch_ids_differ(ev):
        return InheritVerdict(
            NO_INHERIT, CORROBORATED,
            "the branch's own patches CHANGED across the rebase, so conflict "
            "resolution or an edit happened" +
            (" -- the claim of a clean rebase is CONTRADICTED by the artefact"
             if claimed_clean else ""),
            _patch_detail(ev))
    if not claimed_clean:
        # Patch-ids match but the agent reported conflicts. Believe the agent:
        # a resolution can be patch-id-preserving (e.g. taking one side wholesale),
        # and the conservative answer is the one that does not inherit.
        return InheritVerdict(
            NO_INHERIT, CORROBORATED,
            "patch-ids are unchanged but conflicts were REPORTED; a resolution that "
            "happens to preserve patch-ids is still a resolution",
            {"claimed_conflicts": list(ev.claimed_conflicts)})
    return InheritVerdict(
        INHERIT_SOFT, CORROBORATED,
        "patch-ids identical across the rebase: the branch's own patches are "
        "unchanged and only the base moved",
        _patch_detail(ev))


def _patch_ids_differ(ev: RebaseEvidence) -> bool:
    if ev.patch_ids_before is None or ev.patch_ids_after is None:
        return False
    # Multiset, not order: a rebase may reorder nothing, but comparing lists
    # would make an identical set of patches look changed if git emitted them
    # in a different order.
    return Counter(ev.patch_ids_before) != Counter(ev.patch_ids_after)


def _patch_detail(ev: RebaseEvidence) -> dict:
    before = Counter(ev.patch_ids_before or [])
    after = Counter(ev.patch_ids_after or [])
    return {"patch_ids_before": len(list(ev.patch_ids_before or [])),
            "patch_ids_after": len(list(ev.patch_ids_after or [])),
            "lost": sorted((before - after).elements()),
            "gained": sorted((after - before).elements())}


# ------------------------------------------------------------------- the debt


@dataclass
class SoftCommit:
    """A soft-green commit that reached main, and its outstanding debt."""

    commit: str
    landed_at: str                 # ISO-8601Z
    inherited_from: str            # the pre-rebase head whose green was inherited
    basis: str


def debt_state(soft: SoftCommit, *, full_greens_at: set[str],
               later_full_green_on_main: Optional[tuple[str, str]]) -> str:
    """UPGRADED, REDEEMED, or still OUTSTANDING.

    `later_full_green_on_main` is (commit, iso_ts) for the newest full green on
    main, or None. A full green recorded AT the soft commit UPGRADES it; a full
    green on main STRICTLY LATER redeems it, because main having been fully
    green since then means the soft commit's content has been exercised.
    """
    if soft.commit in full_greens_at:
        return DEBT_UPGRADED
    if later_full_green_on_main:
        _, ts = later_full_green_on_main
        if ts > soft.landed_at:
            return DEBT_REDEEMED
    return DEBT_OUTSTANDING


def debt_report(softs: Sequence[SoftCommit], *, full_greens_at: set[str],
                later_full_green_on_main: Optional[tuple[str, str]] = None,
                now: Optional[str] = None) -> dict[str, Any]:
    """How many commits on main are soft, and since when -- the queryable debt.

    `oldest_outstanding` is the answer to "since when", and it is reported even
    when the count is small: a single soft commit that has been outstanding for
    days is a different situation from five from this hour.
    """
    rows = []
    for s in softs:
        state = debt_state(s, full_greens_at=full_greens_at,
                           later_full_green_on_main=later_full_green_on_main)
        rows.append({"commit": s.commit, "landed_at": s.landed_at,
                     "inherited_from": s.inherited_from, "basis": s.basis,
                     "debt": state})
    outstanding = [r for r in rows if r["debt"] == DEBT_OUTSTANDING]
    return {
        "schema_version": 1,
        "total_soft_on_main": len(rows),
        "outstanding": len(outstanding),
        "upgraded": sum(1 for r in rows if r["debt"] == DEBT_UPGRADED),
        "redeemed": sum(1 for r in rows if r["debt"] == DEBT_REDEEMED),
        "oldest_outstanding": min((r["landed_at"] for r in outstanding), default=None),
        "as_of": now,
        "commits": rows,
    }


def render_debt(rep: dict[str, Any]) -> str:
    out = ["SOFT-GREEN DEBT ON MAIN", ""]
    for r in rep["commits"]:
        out.append(f"  [{r['debt'].upper():<11}] {r['commit'][:12]} landed {r['landed_at']} "
                   f"(soft-inherited from {r['inherited_from'][:12]}, basis={r['basis']})")
    out += ["",
            f"soft commits on main: {rep['total_soft_on_main']}  "
            f"(outstanding {rep['outstanding']}, upgraded {rep['upgraded']}, "
            f"redeemed {rep['redeemed']})"]
    if rep["oldest_outstanding"]:
        out.append(f"OLDEST OUTSTANDING: {rep['oldest_outstanding']} -- "
                   "a soft green on main is a debt, not a state to settle into")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--records", help="JSON file: {softs:[...], full_greens_at:[...], "
                                      "later_full_green_on_main:[commit,ts]}")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.records:
        print(__doc__.strip().splitlines()[0])
        print("\nno --records supplied; nothing to report (this is not a green)",
              file=sys.stderr)
        return 2
    data = json.loads(open(args.records).read())
    softs = [SoftCommit(**s) for s in data.get("softs", [])]
    later = data.get("later_full_green_on_main")
    rep = debt_report(softs, full_greens_at=set(data.get("full_greens_at", [])),
                      later_full_green_on_main=tuple(later) if later else None)
    print(json.dumps(rep, indent=2) if args.json else render_debt(rep))
    # Outstanding debt is a non-zero condition: it must not read as success.
    return 0 if rep["outstanding"] == 0 else 1


import sys  # noqa: E402  (kept last so the module docstring leads the file)

if __name__ == "__main__":
    raise SystemExit(main())

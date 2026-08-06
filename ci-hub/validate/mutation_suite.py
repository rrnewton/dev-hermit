#!/usr/bin/env python3
"""MUTATION SUITE FOR GUARDS — a score, not an anecdote.

Planting a violation IS mutation testing. Done ad hoc it yields one data point:
"we planted a violation and it was caught". Done as a suite it yields a
COVERAGE MEASUREMENT: `killed / total`, plus the individual survivors — and every
survivor is a guard reporting health while not guarding.

THE BRACKET IS THREE PARTS, NOT TWO
-----------------------------------
  1. the mutant IS KILLED (the guard refuses it);
  2. THE LEGITIMATE POPULATION SURVIVES, COUNTED -- a guard that kills
     everything passes (1) perfectly and is useless, so a mutant is only scored
     once its guard's population control has been counted;
  3. the mutant CLEANS UP -- nothing planted may outlive the run.

HARD PRECONDITION, LEARNED THE EXPENSIVE WAY
--------------------------------------------
THE MUTANT MUST NOT BE ABLE TO TRIGGER THE LIVE HAZARD. Planting a
`locally-validated` label on a cold PR can satisfy merge-gate and AUTO-MERGE:
planting an AUTHORISATION is unsafe; planting an INERT artifact is safe. That
rule is enforced here rather than remembered -- every mutant declares a
`hazard` class, and `Mutant.__post_init__` REFUSES to construct an
authorisation-capable mutant. A precondition you have to remember is one you
will eventually forget at 3am.

USAGE
  python3 ci-hub/validate/mutation_suite.py            # run, print the score
  python3 ci-hub/validate/mutation_suite.py --json     # machine-readable
  python3 ci-hub/validate/mutation_suite.py --guard qualified_rows
Exit 0 iff every mutant was killed AND every population control held.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
CI_HUB = HERE.parent
PARENT = CI_HUB.parent
for p in (HERE, CI_HUB, CI_HUB / "landing", CI_HUB / "timeout_audit"):
    sys.path.insert(0, str(p))

KILLED = "KILLED"
SURVIVED = "SURVIVED"
SKIPPED = "SKIPPED"

# Hazard classes. Only INERT mutants may be constructed: an INERT mutant cannot
# authorise anything even if it leaked, which is the property that makes the
# suite safe to run unattended.
INERT = "inert"
AUTHORISATION = "authorisation"


class UnsafeMutant(Exception):
    """Refused: this mutant could trigger the live hazard it is testing."""


@dataclass
class Mutant:
    """One planted violation, and the guard that must refuse it."""

    id: str
    guard: str
    # What real defect this encodes -- the sentence a reader needs to know WHY.
    defect: str
    # Returns True when the guard REFUSED the mutant (i.e. the mutant is killed).
    probe: Callable[[], bool]
    hazard: str = INERT
    note: str = ""

    def __post_init__(self) -> None:
        if self.hazard != INERT:
            raise UnsafeMutant(
                f"{self.id}: hazard={self.hazard!r}. Planting an authorisation is "
                "unsafe -- exercise the CONSUMER with an inert fixture or a dry run "
                "instead. Refused at construction, not at review."
            )


@dataclass
class Population:
    """The positive control for a guard: legitimate items that must NOT be flagged."""

    guard: str
    describe: str
    # Returns (flagged, total). `flagged` must be 0 for the control to hold.
    count: Callable[[], tuple[int, int]]


@dataclass
class GuardReport:
    guard: str
    killed: list[str] = field(default_factory=list)
    survived: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    population: Optional[dict] = None


# --------------------------------------------------------------------------- probes
# Each probe returns True iff the guard REFUSED the mutant. Probes are tiny and
# call the REAL guard -- a probe that reimplemented the predicate would score
# itself, which is the failure this suite exists to detect.


def _catalogue() -> tuple[list[Mutant], list[Population]]:
    mutants: list[Mutant] = []
    pops: list[Population] = []

    # ---- guard: landing preflight -----------------------------------------
    try:
        import preflight as PF

        def m(mid, defect, fn, note=""):
            mutants.append(Mutant(mid, "landing-preflight", defect, fn, note=note))

        m("preflight/stale-sha",
          "four handed SHAs went stale in one night; one was quoted into agent "
          "instructions for hours after main advanced twice",
          lambda: PF.check_sha_is_current("a" * 40, "b" * 40).verdict == PF.REFUSE)
        m("preflight/unresolvable-head-is-not-pass",
          "an unanswerable check must not read as a satisfied one",
          lambda: not PF.check_sha_is_current("a" * 40, None).ok)
        m("preflight/zero-executed-tests",
          "--features gating: build ok, target ran, ZERO tests executed, SUCCESS reported",
          lambda: PF.check_green_carries_executed_tests(
              "running 0 tests\ntest result: ok. 0 passed\n").verdict == PF.REFUSE)
        m("preflight/empty-log",
          "THE RECORDED KNOWN GAP: a check that only looks for N==0 passes an EMPTY "
          "log, because there is no zero in it to find",
          lambda: PF.check_green_carries_executed_tests("").verdict == PF.REFUSE)
        m("preflight/absent-log",
          "the other half of the same gap: no evidence is not a pass",
          lambda: PF.check_green_carries_executed_tests(None).verdict == PF.REFUSE)
        m("preflight/merged-flag-without-ancestry",
          "~12 PRs orphaned by force-push on 2026-08-03 were still flagged MERGED",
          lambda: PF.check_landed_by_ancestry(
              pr_state="MERGED", merge_commit_oid="c" * 40,
              is_ancestor=lambda s: False, fetched_fresh=True).verdict == PF.REFUSE)
        m("preflight/stale-remote-is-not-pass",
          "a stale ref answers about the past",
          lambda: not PF.check_landed_by_ancestry(
              pr_state="MERGED", merge_commit_oid="c" * 40,
              is_ancestor=lambda s: True, fetched_fresh=False).ok)
        m("preflight/reverie-patch-override",
          "an uncommitted [patch.\"...reverie.git\"] override riding along in a commit "
          "(live in worktrees/250-delegate/hermit)",
          lambda: PF.check_no_uncommitted_patch_override(
              '+[patch."https://github.com/rrnewton/reverie.git"]\n'
              '+reverie = { path = "../reverie" }\n').verdict == PF.REFUSE)
        m("preflight/byte-identical-branch",
          "opening work that already exists verbatim on the remote (live on #355)",
          lambda: PF.check_no_byte_identical_branch(
              candidate_tree="t1", remote_trees={"fix/a": "t1"}).verdict == PF.REFUSE)

        pops.append(Population(
            "landing-preflight",
            "legitimate inputs that must NOT be refused",
            lambda: (
                sum(1 for ok in [
                    PF.check_sha_is_current("a" * 40, "a" * 40).ok,
                    PF.check_sha_is_current("A" * 40, "a" * 40).ok,   # case is not staleness
                    PF.check_green_carries_executed_tests(
                        "running 36 tests\ntest result: ok. 36 passed\n").ok,
                    PF.check_landed_by_ancestry(
                        pr_state="MERGED", merge_commit_oid="c" * 40,
                        is_ancestor=lambda s: True, fetched_fresh=True).ok,
                    PF.check_no_uncommitted_patch_override("+fn x() {}\n").ok,
                    PF.check_no_byte_identical_branch(
                        candidate_tree="t9", remote_trees={"fix/a": "t1"}).ok,
                ] if not ok),
                6,
            )))
    except Exception as err:                                  # pragma: no cover
        mutants.append(Mutant("preflight/UNAVAILABLE", "landing-preflight",
                              f"import failed: {err}", lambda: False))

    # ---- guard: qualified-rows accessor ------------------------------------
    try:
        import qualified_rows as QR

        def _row(**over):
            r = {"commit": "a" * 40, "finished_at": "2026-08-04T10:00:00Z",
                 "result": "pass", "executed_tests": 10,
                 "gates_run": 5, "gates_expected": 5}
            r.update(over)
            return r

        mutants += [
            Mutant("qualified_rows/zero-executed", "qualified_rows",
                   "a zero-executed run is a no-result wearing a success badge",
                   lambda: not QR.is_qualified(_row(executed_tests=0))),
            Mutant("qualified_rows/partial-gates", "qualified_rows",
                   "a 5-of-6 partial run is incomplete, not a pass",
                   lambda: not QR.is_qualified(_row(gates_run=5, gates_expected=6))),
            Mutant("qualified_rows/bool-executed", "qualified_rows",
                   "True is an int in Python: `True > 0` would sneak a bool through",
                   lambda: not QR.is_qualified(_row(executed_tests=True))),
            Mutant("qualified_rows/not-pass", "qualified_rows",
                   "a non-pass row must never enter the green population",
                   lambda: not QR.is_qualified(_row(result="fail"))),
            Mutant("qualified_rows/no-event-time", "qualified_rows",
                   "ordering by file position instead of event time caused a false "
                   "13h schema-5 producer outage report",
                   lambda: not QR.is_qualified(_row(finished_at=None))),
        ]
        ledger = PARENT / "ignored" / "validate-run-ledger.jsonl"
        if ledger.is_file():
            def _pop():
                rows = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]
                qual = [r for r in rows if QR.is_qualified(r)]
                # The control: a guard that killed everything would qualify 0.
                return (0 if qual else 1, len(rows))
            pops.append(Population("qualified_rows",
                                   "real ledger rows still qualify (guard is not kill-everything)",
                                   _pop))
    except Exception as err:                                  # pragma: no cover
        mutants.append(Mutant("qualified_rows/UNAVAILABLE", "qualified_rows",
                              f"import failed: {err}", lambda: False))

    # ---- guard: green-class provenance -------------------------------------
    try:
        import green_class as GC

        def _grow(**over):
            r = {"commit": "a" * 40}
            r.update(over)
            return r

        mutants += [
            Mutant("green_class/laundered-label", "green_class",
                   "stamping green_class=hard on an inherited row makes it read "
                   "byte-identical to a real green",
                   lambda: GC.derive_class(_grow(
                       green_class=GC.HARD, validated_head_sha="b" * 40,
                       inherited_from={"delta_kind": GC.DELTA_REBASE_ONLY,
                                       "branch_commits": 0}))[0] == GC.REFUSED),
            Mutant("green_class/soft-not-hard", "green_class",
                   "validation that ran on an ancestor is not a measurement here",
                   lambda: GC.derive_class(_grow(
                       validated_head_sha="b" * 40,
                       inherited_from={"delta_kind": GC.DELTA_REBASE_ONLY,
                                       "branch_commits": 0}))[0] != GC.HARD),
            Mutant("green_class/contradiction", "green_class",
                   "claims exact-head validation AND inheritance at once",
                   lambda: GC.derive_class(_grow(
                       validated_head_sha="a" * 40,
                       inherited_from={"delta_kind": GC.DELTA_REBASE_ONLY}))[0] == GC.REFUSED),
        ]
        pops.append(Population(
            "green_class",
            "a legacy row with no provenance fields still derives HARD",
            lambda: (0 if GC.derive_class({"commit": "a" * 40})[0] == GC.HARD else 1, 1)))
    except Exception as err:                                  # pragma: no cover
        mutants.append(Mutant("green_class/UNAVAILABLE", "green_class",
                              f"import failed: {err}", lambda: False))

    # ---- guard: check-outcome classifier -----------------------------------
    try:
        from check_outcome import CheckOutcome, classify_check

        mutants += [
            Mutant("check_outcome/in-flight-stale-success", "check_outcome",
                   "a re-run in flight can still carry the previous attempt's "
                   "conclusion; reading it as PASSED is a false green",
                   lambda: classify_check("in_progress", "success") is CheckOutcome.NO_RESULT),
            Mutant("check_outcome/cancelled-is-not-a-result", "check_outcome",
                   "a cancelled run carries no verdict",
                   lambda: classify_check("completed", "cancelled") is CheckOutcome.NO_RESULT),
        ]
        pops.append(Population(
            "check_outcome",
            "completed checks still resolve normally",
            lambda: (sum(1 for ok in [
                classify_check("completed", "success") is CheckOutcome.PASSED,
                classify_check("completed", "failure") is CheckOutcome.FAILED,
            ] if not ok), 2)))
    except Exception as err:                                  # pragma: no cover
        mutants.append(Mutant("check_outcome/UNAVAILABLE", "check_outcome",
                              f"import failed: {err}", lambda: False))

    # ---- guard: .gitmodules hazard ratchet ---------------------------------
    try:
        import gitmodules_lint as GL

        base = ('[submodule "x"]\n\tpath = x\n'
                '\turl = https://example.com/x.git\n\tupdate = checkout\n')

        def _haz(extra: str) -> bool:
            return bool(GL.lint_file.__wrapped__(extra)) if False else bool(
                [v for e in GL.parse_gitmodules(base + extra) for v in GL.hazards_in(e)])

        mutants += [
            Mutant("gitmodules/shallow", "gitmodules_lint",
                   "a shallow submodule makes cold-clone verify pass by removing the "
                   "history it would have checked",
                   lambda: _haz("\tshallow = true\n")),
            Mutant("gitmodules/update-none", "gitmodules_lint",
                   "update = none leaves an empty directory where a consumer expects a tree",
                   lambda: bool([v for e in GL.parse_gitmodules(
                       base.replace("update = checkout", "update = none"))
                       for v in GL.hazards_in(e)])),
            Mutant("gitmodules/branch-field", "gitmodules_lint",
                   "a branch field turns an exact gitlink into a moving target",
                   lambda: _haz("\tbranch = main\n")),
        ]

        def _gm_pop():
            paths = GL.discover(PARENT)
            flagged = sum(len(GL.lint_file(p)["violations"]) for p in paths)
            entries = sum(GL.lint_file(p)["entries"] for p in paths)
            return (flagged, entries)
        pops.append(Population("gitmodules_lint",
                               "real .gitmodules entries are not flagged", _gm_pop))
    except Exception as err:                                  # pragma: no cover
        mutants.append(Mutant("gitmodules/UNAVAILABLE", "gitmodules_lint",
                              f"import failed: {err}", lambda: False))

    # ---- guard: the LIVE zero-test chain (counter -> ledger consumer) -------
    # The owner recorded "KNOWN GAP: an EMPTY log still passes this check". Probed
    # against the LIVE chain rather than assumed: `executed_test_count` returns
    # None (not 0) for an empty log, so whether the gap is real depends entirely
    # on what the CONSUMER does with None. Testing the consumer is also the safe
    # pattern -- no artifact is planted anywhere near an authorisation.
    try:
        sys.path.insert(0, str(CI_HUB / "remediation"))
        import nonzero_result as NR
        import qualified_rows as QR2
        import anchor_select as AS2

        pred = json.loads((HERE / "qualifying-receipt.json").read_text())

        def _row_exec(v):
            return {"commit": "a" * 40, "finished_at": "2026-08-04T10:00:00Z",
                    "result": "pass", "executed_tests": v, "gates_run": 5,
                    "gates_expected": 5, "profile": "full", "selection_mode": "full",
                    "commit_anchored": True, "tree_dirty": False, "schema_version": 1}

        mutants += [
            Mutant("zero_test/empty-log-is-not-zero", "zero-test-chain",
                   "an empty log must not be read as a counted zero; the counter "
                   "must say 'no evidence' (None), not 'zero tests'",
                   lambda: NR.executed_test_count("") is None),
            Mutant("zero_test/counted-zero-is-detected", "zero-test-chain",
                   "the --features-gating shape: a real 'running 0 tests' banner",
                   lambda: NR.executed_test_count("running 0 tests\n") == 0),
            Mutant("zero_test/consumer-refuses-absent-count", "zero-test-chain",
                   "THE RECORDED GAP, tested at the consumer: a ledger row whose "
                   "executed_tests is null (what an empty log produces) must be "
                   "refused, not waved through for lack of a zero to find",
                   lambda: (not QR2.is_qualified(_row_exec(None)))
                   and (not AS2.row_qualifies(_row_exec(None), pred)[0])),
        ]
        pops.append(Population(
            "zero-test-chain",
            "a genuine count still passes the whole chain",
            lambda: (0 if (NR.executed_test_count("running 36 tests\n") == 36
                           and QR2.is_qualified(_row_exec(36))) else 1, 1)))
    except Exception as err:                                  # pragma: no cover
        mutants.append(Mutant("zero_test/UNAVAILABLE", "zero-test-chain",
                              f"probe unavailable: {err}", lambda: False))

    # ---- guard: the ancestry primitive (a KNOWN LEAK, probed deliberately) --
    try:
        import preflight as PF

        def _pr_head_form_survives() -> bool:
            """The DEFECTIVE form: ancestry tested on the PR HEAD.

            After a rebase replay the head is never an ancestor, so this form
            answers NOT-LANDED for a PR that HAS landed -- the read that scored
            79 unlanded when 46 had landed. The guard that must kill this mutant
            is "never test the head"; `check_landed_by_ancestry` structurally
            cannot, so the mutant is killed by construction. Recorded explicitly
            because the leak lives in ANY OTHER call site that still uses the
            head form, and this suite can only speak for this one.
            """
            asked: list[str] = []

            def spy(sha):
                asked.append(sha)
                return sha == "c" * 40

            PF.check_landed_by_ancestry(
                pr_state="MERGED", merge_commit_oid="c" * 40, pr_head="d" * 40,
                is_ancestor=spy, fetched_fresh=True)
            return asked == ["c" * 40]

        mutants.append(Mutant(
            "ancestry/pr-head-form", "ancestry-primitive",
            "testing ancestry on the PR head is ALWAYS false after a rebase replay; "
            "that misread 79 PRs as unlanded when 46 had landed",
            _pr_head_form_survives,
            note="kills only the preflight's call site; other call sites are NOT covered"))
    except Exception:                                         # pragma: no cover
        pass

    return mutants, pops


# --------------------------------------------------------------------------- runner


def run(only_guard: Optional[str] = None) -> dict[str, Any]:
    mutants, pops = _catalogue()
    if only_guard:
        mutants = [m for m in mutants if m.guard == only_guard]
        pops = [p for p in pops if p.guard == only_guard]

    reports: dict[str, GuardReport] = {}
    for m in mutants:
        rep = reports.setdefault(m.guard, GuardReport(m.guard))
        try:
            killed = bool(m.probe())
        except Exception as err:
            rep.skipped.append({"id": m.id, "error": f"{type(err).__name__}: {err}"})
            continue
        if killed:
            rep.killed.append(m.id)
        else:
            rep.survived.append({"id": m.id, "defect": m.defect, "note": m.note})

    for p in pops:
        rep = reports.setdefault(p.guard, GuardReport(p.guard))
        try:
            flagged, total = p.count()
            rep.population = {"describe": p.describe, "flagged": flagged,
                              "total": total, "holds": flagged == 0}
        except Exception as err:
            rep.population = {"describe": p.describe, "error": str(err), "holds": False}

    killed = sum(len(r.killed) for r in reports.values())
    survived = sum(len(r.survived) for r in reports.values())
    skipped = sum(len(r.skipped) for r in reports.values())
    scored = killed + survived
    pops_total = sum(1 for r in reports.values() if r.population is not None)
    pops_holding = sum(1 for r in reports.values()
                       if r.population is not None and r.population.get("holds"))

    return {
        "schema_version": 1,
        "mutation_score": {
            "killed": killed,
            "total_scored": scored,
            "skipped": skipped,
            "percent": round(100.0 * killed / scored, 1) if scored else None,
        },
        "population_controls": {"holding": pops_holding, "total": pops_total},
        "survivors": [s for r in reports.values() for s in r.survived],
        "guards": {
            g: {"killed": r.killed, "survived": r.survived, "skipped": r.skipped,
                "population": r.population}
            for g, r in sorted(reports.items())
        },
    }


def render(rep: dict) -> str:
    s = rep["mutation_score"]
    out = ["MUTATION SUITE FOR GUARDS", ""]
    for g, d in rep["guards"].items():
        pop = d["population"]
        if pop is None:
            pop_s = "population control: NONE (guard scored on mutants only)"
        elif pop.get("holds"):
            pop_s = (f"population control HOLDS: {pop['flagged']} flagged of "
                     f"{pop['total']} legitimate")
        else:
            pop_s = f"population control FAILED: {pop}"
        out.append(f"  {g}: killed {len(d['killed'])}/"
                   f"{len(d['killed']) + len(d['survived'])}  |  {pop_s}")
        for sv in d["survived"]:
            out.append(f"      SURVIVED  {sv['id']} -- {sv['defect']}")
        for sk in d["skipped"]:
            out.append(f"      SKIPPED   {sk['id']} -- {sk['error']}")
    out += ["",
            f"MUTATION SCORE: {s['killed']}/{s['total_scored']}"
            + (f" ({s['percent']}%)" if s["percent"] is not None else ""),
            f"POPULATION CONTROLS: {rep['population_controls']['holding']}/"
            f"{rep['population_controls']['total']} holding"]
    if s["skipped"]:
        out.append(f"SKIPPED (uncounted, NOT killed): {s['skipped']}")
    if rep["survivors"]:
        out.append("")
        out.append("SURVIVORS -- each is a guard reporting health while not guarding:")
        for sv in rep["survivors"]:
            out.append(f"  - {sv['id']}: {sv['defect']}")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--guard", help="run only one guard's mutants")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rep = run(args.guard)
    print(json.dumps(rep, indent=2) if args.json else render(rep))

    s = rep["mutation_score"]
    ok = (s["killed"] == s["total_scored"] and s["skipped"] == 0
          and rep["population_controls"]["holding"] == rep["population_controls"]["total"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

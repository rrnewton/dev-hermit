# Adversarial review: landing-soundness & ledger artifacts

**Date:** 2026-08-05 · **Task:** `adv-review-landing-ledger-artifacts` · **Reviewer lane:** ci/ledger
**Method:** plant-a-violation + positive control, per artifact. Local only, no egress, live ledger
never written. **Status:** committed to the parent, **not pushed** (egress 403).

## Verdict table

| # | artifact | verdict | denominator |
|---|---|---|---|
| 1 | `qualified-rows` guard | **REAL** | 18/18 data mutants refused; 6/6 clause mutants caught; 107/585 rows qualify |
| 2 | anchor-selection-cheapest-green | **REAL, 1 divergence** | 7/9 planted refused; 107/585; **70/107 anchors rest on the weak fallback** |
| 3 | qualified-rows mutation-bracket doc | **CORROBORATED** | independently replicated 107/585 and 6/6 |
| 4 | verify-field / log-strip audit | **CORROBORATED — its A6 finding is REAL** | greedy regex confirmed behaviourally |
| 5 | validate-then-land-unsound | **NOT REVIEWED IN DEPTH** | task still `in_progress`; stated, not assessed |
| 6 | vacuous-test audit | **LIGHT CHECK ONLY** | exists; T2 stranded |
| 7 | stranded-uncommitted rescue | **CONFLICTED — self-review** | authored by me this session; needs another reviewer |

## The cross-cutting finding: the guards are real, and almost none of them are on main

| artifact | tracked | on `origin/main` |
|---|---|---|
| `qualified_rows.py` (base) | yes | **yes** |
| `qualified_rows.py` soft-green clause | **no — uncommitted edit** | **no** |
| `green_class.py` (its dependency) | **no — untracked** | **no** |
| `anchor_select.py` | **no — untracked** | **no** |
| verify-strip / duplication / mutation-bracket docs | yes | **no — unpushed** |
| vacuous-test audit | yes | **no — unpushed** |

**Six of seven artifacts are stranded off main.** These are real, working guards that protect
exactly one checkout. This is the same orphaned-front-door pattern as the stranded-work task,
and it is the single most consequential thing in this review: a guard that is not on main is
not a guard, it is a local habit.

## 1. `qualified-rows` guard — REAL

Live denominator: **585 rows, 0 malformed, 107 qualified (18.3%)**.

Method: take a **real** row the guard accepts, break exactly one clause at a time. That isolates
each clause's discriminating power instead of testing the conjunction as a lump.

Refused: `result=fail`; `result` missing; `executed_tests=0`; `executed_tests=True` (bool-is-int
trap); `executed_tests="786"`; `executed_tests` missing; `gates_run=3` vs expected 6;
`gates_run=5` of 6; `gates_expected=0`; non-int `gates_run`; naive/unparseable/missing
`finished_at`; SOFT provenance stamped `green_class=hard`. Accepted: the unmutated row, and
`gates_run=6` of 6. **18/18 correct, zero real gaps.**

Clause-deletion bracket (isolated copy; the tracked file was never mutated): deleting any of
`result=="pass"`, `not isinstance(executed,bool)`, `executed>0`, `ran>=expected`,
`green_class==HARD`, `event_time(row) is not None` causes at least one test to fail. **6/6 — no
decoration clauses.** Baseline 12 passed.

**My first plant was invalid, and it is worth recording because it is the same proxy-binding
error these guards exist to catch.** I mutated `checks=3, gates_expected=6` and the row still
qualified, which looked like a hole. It was not: `gate_counts()` reads `gates_run` first and only
falls back to `checks`, so the mutation never reached the resolver. **`checks` is a shadowed
field** — any consumer reading it instead of `gates_run` can disagree with the authority.

### The soft-green clause: REAL predicate, currently INERT, and stranded

- `origin/main`'s `qualified_rows.py` does **not** import `green_class` and has no soft-green
  clause. **Main is not broken** — I imported main's copy in isolation to confirm there is no
  import breakage. No overclaim.
- The refusal exists only as an **uncommitted +13-line edit** depending on an **untracked**
  `green_class.py`.
- **Exposure today is zero.** Main qualifies 107; the guarded local build qualifies the same 107;
  rows admitted by main but refused locally = **0**. `green_class` over all 585 rows is
  `{hard: 585}` — no row carries `inherited_from`/`validated_head_sha`, so nothing derives SOFT.

So: a real predicate, inert on the current population, absent from main. It becomes load-bearing
the moment a producer emits inherited greens — and it must be on main *before* that, not after.

## 2. anchor-selection — REAL, with one genuine cross-authority divergence

Refused: `executed_tests=0`; `commit_anchored=False`; `tree_dirty=True`; `result=fail`;
`selection_mode=selective` (would chain two hops); `coverage.zero_executed_nodes`;
`coverage.absent_nodes`; coverage removed entirely. The per-node coverage clause is enabled and
strong.

**The one real gap — the bool trap.** `executed_tests=True` **qualifies** for the anchor picker
but is **refused** by the canonical `is_qualified`. `True == 0` is False so the zero-guard misses
it; `isinstance(True,int)` is True; `True < 1` is False. Two authorities answering the same
question differently is precisely the one-verifier-per-authority violation. One-line fix.

Two apparent gaps did **not** survive triage:
- *"partial run `gates_run=1` of 6 qualifies"* — `anchor_select` deliberately does not read
  `gates_run`; it decides completeness by per-node **coverage**, which the predicate argues (with
  landed evidence: 8 PASS rows at `executed=427` but coverage 4/19 planned, 15 absent) is the
  right signal and that counts are proxies in both directions. My plant mutated a field the
  predicate does not consult. A real partial run has broken coverage and **is** refused.
- *`finished_at` garbage qualifies* — no event-time clause; affects recency/ordering, not
  green-ness. Canonical refuses it.

**The exposure worth flagging (shared by both authorities, so not a divergence):** coverage
applies only at `schema_version >= 5`. Schema distribution over 585 rows is
`{1:76, 2:20, 3:343, 4:97, 5:49}`, and of the 107 anchor-qualifying rows **70 are schema 3 or 4**.
So **65% of currently-selectable anchors qualify via the weak pre-coverage fallback
(`executed_tests >= 1`) with no per-node coverage requirement.** A planted schema-4 receipt with
`executed_tests=1` and no coverage block at all qualifies as a green anchor. The predicate
explicitly accepts this ("the strongest thing they can prove"), so it is deliberate — the
*denominator* is the news: the strong rule governs only 37 of 107 selectable anchors.

## 4. verify-field / log-strip audit — its headline finding is REAL

The audit calls `RE2` (`logdiff.rs:223`) "MASKING-A-HOLE (worse than documented)". Confirmed
behaviourally rather than by reading:

```
pattern /tmp/.*"   applied to:  open("/tmp/a") then write("KEEPME") and "/tmp/b"
                      result:  open("/tmp/<somewhere>
```

Greedy `.*` spans from the first `/tmp/` to the **last** quote on the line, deleting everything
between. In `Stripped` compare mode a genuine divergence sitting between a tmp path and a later
quote is **erased before comparison**, so verify reports determinism it did not establish. That
is a false-green mechanism in the verify path, and the audit is right to call it worse than a
plain over-strip.

## 5-7. Stated limits of this review

- **`validate-then-land-unsound`: not reviewed in depth.** The task
  (`validate-then-land-is-unsound-the-push-rewrites-the-head`) is still `in_progress` and the
  design doc (`validate-final-candidate-before-landing-design-20260805.md`) describes a proposal,
  not a shipped guard. There is no consumer to plant against yet. Saying so beats manufacturing
  a verdict.
- **Vacuous-test audit: light check only.** Present and coherent; T2 stranded. Not
  independently replicated — out of budget, and I would rather label the depth than imply it.
- **Stranded-uncommitted rescue: I wrote it earlier this session.** Its selftest re-runs
  22/22, but an author adversarially reviewing their own artifact is not an independent check —
  the failure modes I would look for are the ones I already thought of. **Reassign to another
  reviewer.** Flagging rather than quietly self-certifying.

## Recommended actions, in order

1. **Land the stranded guards.** `green_class.py`, the `qualified_rows.py` soft-green edit, and
   `anchor_select.py` are untracked/uncommitted; the doc audits are unpushed. Blocked on egress.
2. **Fix the bool trap** in `anchor_select.row_qualifies` — one line, removes a live
   cross-authority divergence.
3. **Decide the pre-schema-5 anchor policy.** 65% of selectable anchors bypass the coverage rule.
   Either accept it explicitly with the denominator stated, or require schema ≥ 5 for anchors.
4. **Bound `RE2`** in `logdiff.rs` (non-greedy / no-quote character class) — it can mask a real
   divergence.
5. **Reassign artifact 7** to a reviewer who did not write it.

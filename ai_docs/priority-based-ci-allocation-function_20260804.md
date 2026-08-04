# Priority-based CI allocation: the derived function

**Task:** `priority-based-ci-planner-owns-the-batch` (owner P0). **Author:** hermit-243. **Date:** 2026-08-04.

## Problem (owner's framing)

Hosted CI capacity currently goes to the **most-recently-pushed** head, not the head **closest to
landing**. Random feature-branch CI competes on equal footing with the batch the planner wants to
drain. We must **derive** an allocation priority function from measured inputs — not pick a
hand-tuned weighting, which would be "the conservative-constant defect in a new field."

## The three measured inputs (with provenance)

### Input A — the anchor split (MEASURED this session, not handed)

Of **75 open hermit PRs**, only **19 are anchor-OK** (head contains `bfb0a9ef`, so the validate
writer emits non-null anchor fields and the receipt is *schema-admissible*). **56 predate** the
anchor and are **structurally unvalidatable** — no rebase, no qualifying receipt is possible.

- Anchor-OK ready: **10** — `[1581,1576,1571,1555,1514,1471,1470,1468,1412,1365]`. Anchor-OK draft: 9.
- Method: bulk-fetch `refs/pull/*/head`, `git -C hermit merge-base --is-ancestor bfb0a9ef <ref>`;
  heads snapshot in `ignored/open-prs-anchor-analysis.json`. (Owner's handed "57/74 predate" ≈ the
  measured 56/75 — corroborated.)
- **Compound eligibility caveat:** anchor-OK is *necessary, not sufficient*. A qualifying receipt
  also needs the reverie pin bump `525627be` (else validate-in-place re-injects the `detcore_misc`
  livelock — the #1567 trap: **negative** expected value, not zero). `#1571` is anchor-OK but
  known-FAILED. So the true *landable-now* set is **well below 19**.

**Contribution:** A is the **eligibility gate**. Spending a scarce admission slot on a predating
head yields nothing and can actively re-trigger a livelock. The gate removes 56/75 from contention
before any ranking happens, and turns each excluded PR into an **actionable rebase signal**.

### Input B — admission is latency-bound, not waste-bound (MEASURED; handed premise refuted)

The handed premise ("each superseded commit burns 35 runs") is **refuted** by the measured ledger
([[portable-ci-admission-limited-derived-ceiling-17]]): 31/32 superseded portable runs instantiated
**zero** jobs; netted burst-waste ≈ **1 full-run/18h**; flipping `cancel-in-progress` saves ≈ 0.

The real binding constraint is **admission latency** under the shared-free-pool throttle (~8
concurrent): saturated first-job **22 min**, p50 **36 min**, max **51 min**.

**Contribution:** B sets the **cost of losing the priority race** — a 22–51 min queue wait, not
wasted compute. That makes an admission slot a scarce, expensive resource whose *allocation order*
is the whole lever. It also justifies a **stability** tie-break: under `cancel-in-progress:false`, a
volatile head that gets re-pushed forfeits its expensive slot.

### Input C — width is exhausted; order is the only lever (MEASURED)

Busy-wall is **serial-spine-limited**: `backend build` 320 s + `strict-compat` 119 s = **439 s** that
no runner count can cut; realized peak-17 held only **3.4 %** of wall; demand-weighted pool is
**~8–12**, not 17 ([[portable-ci-admission-limited-derived-ceiling-17]],
[[two-level-parallelism-outer-times-inner-and-serial-tail]]).

**Contribution:** C proves we **cannot buy our way out with width**. Given fixed, scarce capacity,
the *ordering* of who gets admitted is the only remaining control — which is exactly what a priority
function governs.

## The derived function (lexicographic — not a weighted sum)

A weighted sum forces invented coefficients (the defect the owner named). The inputs are not
commensurable — A is a hard admissibility fact, B a cost, C a proof that order is the only lever —
so they compose as a **lexicographic order** with a hard gate:

```
TIER-0  ELIGIBILITY GATE (from A):
        admissible  ⇔  head is anchor-OK (contains bfb0a9ef) AND carries 525627be
                        (i.e. rebased onto current main so a QUALIFYING receipt is possible).
        Inadmissible ⇒ 0 hosted capacity + emit a REBASE signal. (Un-rebased = negative-EV.)

Among admissible PRs, rank by:
  KEY-1  batch membership     — the planner's named batch first (ci-hub umbrella / ci-batch label).
  KEY-2  closeness-to-landing — fewest remaining required runs to green; landing = realized yield,
                                and (from B) each admission is a scarce 22–51 min slot, so spend it
                                on the PR nearest to converting.
  KEY-3  head-stability       — tie-break: prefer stable heads (from B: volatile heads under
                                cancel-in-progress:false waste the slot).
```

C is the meta-justification: because width is exhausted, this **order** is the entire mechanism.

## Verification — BOTH directions

- **Fires (landable ahead of unvalidatable):** an admissible, batch, near-landing PR outranks a
  predating head on every key, and predating heads are gated out entirely. Proven by TIER-0 + KEY-2.
  Concretely today: any of the 10 anchor-OK ready heads outranks all 56 predating heads.
- **Non-starvation (tail still runs), N STATED:** add **AGING** — reserve ≥1 admission slot per
  cycle for the **oldest-waiting admissible** PR. Then every one of the **N = admissible tail**
  (today **~10 anchor-OK ready**, minus the current batch *b*) is guaranteed a turn within
  **≤ (N − b) aging cycles**. N is measured at plan time from the anchor split, not assumed.

## What this does NOT do

- Does not touch `cancel-in-progress` (Input B: ≈0 gain, refuted).
- Does not add runners as the fix (Input C: serial spine uncuttable; pool already ~sufficient when
  not saturated).
- Does not rank predating heads at all — it converts them to rebase work, which is the real
  precondition for them to ever become admissible.

## Coordination

Admission-limit measurement is owned by **hermit-ghdag**; the Input B numbers above carry their own
provenance ([[portable-ci-admission-limited-derived-ceiling-17]]). If ghdag revises the throttle
peak or latency distribution, only the *cost magnitude* behind KEY-2/KEY-3 changes — the
lexicographic **structure** (gate → batch → closeness → stability + aging) is derived from the
*shape* of the inputs, not their exact constants, so it is robust to that revision.

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

## Tonight's inputs, sharpened (2026-08-04, three measured inputs → three contributions)

The three inputs handed this session re-key the derivation on the **reverie pin** and give the gate
a *quantitative* justification (a livelock ratio) it previously argued only qualitatively. The
lexicographic structure is unchanged; the inputs make it tighter. Each input contributes exactly one
thing:

### Input 1 — the reverie pin split → the ELIGIBILITY GATE, now quantified

**Measured (this session):** of **73 open PRs, 59 carry the unfixed reverie pin `d973a85b`** and
**14 carry the fix**. The unfixed pin's `detcore_misc` livelock rate is **19.4 %**; with the fix it
is **0.10 %** — a **~194× differential**.

**Contribution:** this is the gate, and the 194× ratio is *why the gate is not optional*. A PR on the
unfixed pin does not "waste a slot" neutrally — it converts a scarce admission into a 19.4 %-chance
livelock, i.e. **negative** expected value, not zero. The remedy is a **pin BUMP, not capacity**:
handing these 59 more hosted slots cannot help them: they will livelock ~1 in 5 times regardless of
how much capacity they get. Only rebasing onto the fix moves them. So the gate **removes 59/73 from
contention up front** and emits each as an actionable *bump* signal — capacity spent on them is worse
than idle. This aligns with the earlier anchor-split (Input A): anchor-OK is schema-admissibility;
pin-fixed is livelock-freedom; **eligibility requires BOTH**, and the pin is the binding one tonight
(59 fail it vs 56 predating the anchor).

### Input 2 — admission caps at 8; portable is 90 % of it → the COST that makes ORDER the lever

**Measured:** hosted admission caps at **8 concurrent**, and **portable runs are 35/39 = 90 % of
per-push admissions**.

**Contribution:** this fixes *where* the scarce resource actually is and *how* scarce. The contested
resource is not "CI" in the abstract — it is the **8-wide portable admission window**, which one lane
saturates 90 % of. That is what makes losing the priority race expensive (Input B's 22–51 min queue
wait is *this* window filling). Because the window is narrow and one-lane-dominated, **admission
order into it is the entire allocation control** — there is no slack to absorb a bad ordering.

### Input 3 — outer DAG ceiling 4.24×, width exhausted → ORDER is the ONLY lever

**Measured:** outer DAG parallelism tops out at **4.24×**
([[two-level-parallelism-outer-times-inner-and-serial-tail]]); width is exhausted.

**Contribution:** this closes the escape hatch. We cannot widen our way out of the 8-wide window —
the DAG itself caps at 4.24× and the serial spine (Input C) is uncuttable. With width fixed,
**ordering is the only remaining degree of freedom**, which is precisely what a priority function
sets. Inputs 2 and 3 together prove the lever is *order*; Input 1 sets *who is even eligible* to be
ordered.

### Restated function, verified both directions with N

The function is unchanged: **TIER-0 gate** (anchor-OK **AND** reverie-pin-fixed) → **KEY-1** batch →
**KEY-2** closeness-to-landing → **KEY-3** head-stability, plus **AGING**.

- **Fires:** a pin-fixed, batch, near-landing PR wins every key; the 59 unfixed-pin PRs are gated out
  before ranking (admitting one is *negative*-EV at 19.4 % livelock). Proven by TIER-0 + KEY-2.
- **Non-starvation, N STATED:** AGING reserves ≥1 of the 8 admission slots per cycle for the
  **oldest-waiting eligible** PR. The eligible tail is bounded by **Input 1's 14 pin-fixed PRs**
  (of which ~10 are also anchor-OK and ready today); so **N = 14** (≤ 14 admissible, ~10 ready). With
  batch size *b*, every eligible PR gets a turn within **≤ (N − b) aging cycles** — the low-priority
  tail is delayed, never starved. A scheduler that starved this tail would be disabled; AGING is why
  it is not.

## Coordination

Admission-limit measurement is owned by **hermit-ghdag**; the Input B numbers above carry their own
provenance ([[portable-ci-admission-limited-derived-ceiling-17]]). If ghdag revises the throttle
peak or latency distribution, only the *cost magnitude* behind KEY-2/KEY-3 changes — the
lexicographic **structure** (gate → batch → closeness → stability + aging) is derived from the
*shape* of the inputs, not their exact constants, so it is robust to that revision.

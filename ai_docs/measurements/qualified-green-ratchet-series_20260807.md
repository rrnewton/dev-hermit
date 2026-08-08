# Qualified-green ratchet: the series, its floor, and one unearned increment

**Task:** `publish-the-ratchet-with-its-floor-and-first-increment` · hermit-w11
(`[impl agent, opus-5]`) · **2026-08-07**

This is the **series**. Each point carries the definition it was measured under,
identified by SHA, so that a tightening can never read as a regression and a
regression can never be "fixed" by loosening the definition back.

**Monotonicity is claimed only within a definition block.** Points in different
blocks are not comparable and are segregated below rather than plotted together.

---

## The series

### Definition block **D1 — qualified green** (current)

> Green requires `comparison_tier ∈ {full-stdout-info-stack-heap,
> stdout-info-stack-heap-spot-check}`.

| # | date | value | measured / total | definition SHA | evidence |
| --- | --- | ---: | --- | --- | --- |
| **D1.0** | 2026-08-07 | **0** | **0 / 2284** rows · 0 / 2184 executed · 0 / 1837 raw passes | defn `b7e92321` · gate blob `ba8c1733` (`check-scorecard-tier.py`, last changed `0c38fb37`) | [`tightened-emission-rules-one-clean-drop_20260807.md`](tightened-emission-rules-one-clean-drop_20260807.md) |

**Floor = 0, and that is honest.** All 2284 rows carry
`comparison_tier=legacy-unqualified` — an explicit known non-green
classification, not a blank and not a schema violation. The cells ran; 1837
still pass on their raw outcome. What is zero is the count whose *comparison was
strict enough to qualify*. Nothing has yet been measured with a qualifying
comparator, so the honest qualified count is zero and every future qualifying
measurement ratchets **up** from here.

`executed 2184 + no-result 100 = 2284`, asserted per scorecard, not only on the
total.

### Definition block **D0 — raw pass** (superseded, weaker, shown for continuity)

> Green = the cell's raw outcome was `pass`. No comparison-tier requirement;
> comparison was stdout-only and `verify_compare=stripped`.

| # | date | value | measured / total | definition SHA |
| --- | --- | ---: | --- | --- |
| D0.a | 2026-08-06 | 794 | 794 / 1025 rows (205 effective cells × 5 backends) | baseline `3825d05d` |
| D0.b | 2026-08-07 | 1837 | 1837 / 2284 rows | defn `b7e92321` §1 |

**D0 → D1 is not a regression.** `1837 → 0` is a definition change, and the two
numbers answer different questions: *did the cell pass its weak comparison* vs
*was the comparison strict enough to count*. Quoting the drop as a compatibility
loss would be wrong in exactly the way this series exists to prevent.

D0.a and D0.b are also **not comparable to each other** — 1025 rows over five
backends versus 2284 over six, with a five-test corpus dedup between them. They
sit in the same block because they share a definition, not because they share a
population.

---

## Rules this series enforces

1. **A point states its definition SHA.** A point without one is not admissible.
2. **A point measured under a different definition is segregated**, never
   appended to the running series. New definition ⇒ new block ⇒ new floor.
3. **Every point carries measured / total.** A bare count is not a point.
4. **Within a block the series is monotonic non-decreasing.** A decrease inside
   a block is a regression and must be investigated, not re-based.
5. **Loosening a definition to recover a number is prohibited.** The recovery
   would appear as a new block starting from its own floor, which makes the
   manoeuvre visible rather than hidden.

## Other live ratchets — deliberately kept separate

Neither belongs in this series; both have their own populations and definitions.

- **prefix-parity first-divergence** — record 20 → >139 on heapwalk, **one
  guest**, INFO-log `COMMIT` depth.
- **the ptracer ratchet** — separate axis.

---

## The first increment: attempted, **not earned**

The instruction was to earn the first increment where the evidence is cheapest
and strongest. I attempted exactly that and did not get it. Reporting the
attempt because a failed attempt at a known-cheapest cell is information about
where the frontier actually is.

**Cell chosen:** a small static guest with no `RDTSC`
(`scratch/w11-tsc/notsc`, compiled `-O0 -static`), ptrace as reference against
**e9patch** — chosen because e9patch is the cheapest backend to qualify here:
it runs today, needs no fix, and preprocesses to the ptrace runtime, so the two
sides should be closest. Two runs per backend at hermit `590fcc9e`
(`--features third-party-backends`), `--strict --detlog-stack --detlog-heap`.

**Precondition — both sides self-deterministic: PASS.** This is new information
and it is a real step:

| backend | stdout | info | stack | heap | verdict |
| --- | --- | --- | --- | --- | --- |
| ptrace | = | = | = | = | **PASS** |
| **e9patch** | = | = | = | = | **PASS** |

e9patch self-determinism across all four signals was previously **unmeasured**;
it now has a measured PASS on this guest.

**Qualification — all four signals vs the reference: FAIL, 1 of 4.**

| signal | ptrace | e9patch | |
| --- | ---: | ---: | --- |
| stdout | `6e340b9c…` | `6e340b9c…` | **MATCH** |
| info (DETLOG syscall records) | 33 | **249** | DIFFER |
| stack hashes | 16 | **124** | DIFFER |
| heap hashes | 14 | **12** | DIFFER |

So **the floor stays 0**. The cell does not qualify, and I am not recording it
as an increment on the strength of stdout alone — that is precisely the
stdout-only comparison D1 was created to stop counting.

**What the numbers say about why.** e9patch emits 249 INFO records against
ptrace's 33 and 124 stack hashes against 16 — roughly 7.5× and 7.75×, not a
small delta. That is far larger than the known "+9 syscalls from the e9patch
runtime" and suggests the preprocessed image's own activity is inside the
compared window, not merely appended to it. Establishing whether that is a
comparator scoping question or a real behavioural divergence is the next step,
and it is cheap: it is one guest and four numbers.

**Where the first increment is most likely to come from**, in cost order:

1. **Narrow the compared window for e9patch** so the preprocessing runtime's own
   records are outside it — if the 249-vs-33 gap is scoping, this cell qualifies
   immediately and the increment is 1.
2. **KVM stack after reverie#403** — a *derived prediction of 3 cells* already
   exists (`trivial/stack`, `threaded/stack`, `heap_exercising/stack`), gated on
   the merge commit, not on the PR passing review.
3. **A spot-check tier cell**, which is the cheaper of the two qualifying tiers
   and has no measured instance yet.

## Reproduction

```bash
B=scratch/w11-corpus/target/release/hermit      # 590fcc9e, --features third-party-backends
export HERMIT_INSTALL_DIR=scratch/w11-corpus/target/install_pkg
for bk in ptrace e9patch; do for rep in 1 2; do
  $B --log=info --log-file=q.$bk.$rep.log run --backend $bk --strict \
     --detlog-stack --detlog-heap --tmp=/tmp -- $PWD/notsc >q.$bk.$rep.out
done; done
# compare stdout / DETLOG[syscall] / [stack]-> / [heap]-> across runs and backends
```

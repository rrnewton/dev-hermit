# Per-tightening cell-class deltas: what each definition change actually cost

**Task:** `count-cells-that-changed-class-under-the-tightenings` · hermit-w11
(`[impl agent, opus-5]`) · **2026-08-07** · local, read-only, no egress, nothing mutated.

**Population `P` (held fixed for every row of every table below):** **1025 rows** =
205 effective cells × 5 backends, from
`compat-envelope/pre-tightening-baseline-20260806/scorecard.csv` at parent
`origin/main`, sweep `run_id=pre-tightening-baseline-20260806`, Hermit
`4c70658e7`, Reverie `dd3c178e`.

**Baseline partition of `P`:** green **794** + not-green **231** = **1025**. Every
table below re-partitions these same 1025 rows. Unaccounted: **0**.

---

## 0. The headline, because three different "drops" are in circulation and two are traps

| candidate "the drop" | value | verdict |
| --- | --- | --- |
| `926/1200 → 795/1025` | −131 | **TRAP.** Almost entirely KVM leaving the matrix (130 greens → 0 rows). Not a regression. |
| four comparable backends | −1 | **TRAP.** True but inside per-cell churn (§4), and it measures the *corpus* change, not a tightening. |
| `346 claimed bitwise → 0` | −346 | **TRAP, different population.** That is `compat-envelope/scorecard.csv` (618 rows), not `P`. Excluded by construction. |
| **stripped is not a falsifiable green** | **794 → 0** | **The real bound.** §3. |

**No tightening measured here cost between 1 and 793 greens.** Every definition
change that has actually been *applied* to `P` cost **zero** verdicts; the one
that has not yet been applied costs **all of them**. There is no gentle middle,
and a reader looking for "the one clean drop" should be told that number is
**794**, not −1.

---

## 1. Per-tightening delta table

Each row applies one definition change to the same 1025 rows.

| # | tightening | rows touched | green → NOT-COMPARABLE | green → refused | label-only, verdict unchanged | net green delta |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| T1 | **corpus**: a cell counts only if its guest source exists at the measured SHA; every unproducible cell is carried as an explicit no-result row | 0 | 0 | 0 | 0 | **0** |
| T2 | **tier**: state the comparison each cell *earned*, not the one it claimed | 1025 | 0 | 0 | **1025** | **0** |
| T3 | **blank evidence**: refuse a determinism positive whose evidence fields are blank | 0 | 0 | **0** | 0 | **0** |
| T3′ | *same rule read as "produced no output"* | 29 | 0 | **29** | 0 | **−29** |
| T4 | **0/0 dimension**: refuse a verdict with a zero denominator | 297 | 0 | 0 | 297 | **0** |
| T5 | **relaxation recorded**: `verify_compare` must be recorded — and `stripped` is a relaxation | 1025 | 0 | **794** | 0 | **−794** |
| T6 | **self-determinism**: two runs must agree before a parity figure is emitted | — | — | — | — | **NOT COMPUTABLE** |

### Traceability — each delta to its specific cause

- **T1 = 0 verdicts.** 235 nominal → 205 effective. The 30 `performance/*` cells
  (×5 = 150 rows) were never in `P`; their fixtures do not exist at the measured
  SHA. This moved the **denominator only**: +9.9 pp aggregate with *not one*
  additional cell passing. A percentage-point gain with zero verdict changes is
  the signature of a denominator move and must never be read as improvement.
- **T2 = 0 verdicts, 1025 labels.** All 1025 rows gained `observed_tier` and
  `bitwise_axis`: 583 `TIER-1-AT-BEST`, 145 `TIER-0-FAIL`, 297
  `NO-RESULT-UNMEASURED`; `bitwise_axis = CONTRACT-UNAVAILABLE` on **1025 of
  1025**. `TIER-1-AT-BEST` is an *upper bound on the claim*, never an achieved
  tier — stdout-sha256 cannot establish better, and TIER-2/+stderr and
  TIER-3/+INFO were never compared.
- **T3 = 0, and the distinction is load-bearing.** Greens with a genuinely
  **blank** evidence field: **0**. But **29** greens carry
  `output_hash = e3b0c442…`, the sha256 of the *empty string* — a pass whose
  evidence is the hash of nothing. Split by backend: ptrace 8, e9patch 8, dbi 7,
  sabre 5, liteinst 1. Whether these are refused depends on whether the rule
  tests *"field is blank"* (0 refused) or *"guest produced no output"* (29
  refused). **The rule as written catches none of them.** That gap is the
  finding; the 29 are listed so the choice is made deliberately rather than by
  accident of phrasing.
- **T4 = 0 verdicts.** 297 rows are already `NO-RESULT-UNMEASURED` on parity and
  were never counted green, so refusing zero-denominator verdicts removes
  nothing further from `P`. It is already satisfied here.
- **T5 = 794, the whole green set.** `verify_compare = stripped` on **1025 of
  1025** rows — there is no unrelaxed row in this corpus. The published banner
  states the consequence: under `--verify-strict` a control catches **all five**
  planted defects while the stripped probe **misses three** (DETLOG-only,
  address, path divergence), each reporting `Success: deterministic. Determinism
  verified.` So every one of the 794 greens was scored by a probe that cannot
  detect three of five known defect classes. **Under a falsifiability-backed
  definition, `P` contributes 0 greens, not 793.**
- **T6 is not zero — it is unmeasured, and the difference matters.** Every row in
  `P` is a **single run**; rows carrying two-run data: **0 of 1025**.
  Self-determinism cannot be evaluated over this population at all. The measured
  5-of-12 NOT-COMPARABLE result lives on a *different population* (guest `heapy`,
  2 backends, 2 dimensions, 12 cells) and **must not be imported into this
  table** — that is precisely the `346/618`-vs-`128/133` error the task warns
  against.

---

## 2. Pre- and post-tightening totals, both stated

| | greens | population | definition |
| --- | ---: | ---: | --- |
| pre-tightening | **794** | 1025 | `deterministic == 1` under stripped-verify-L2 |
| post, T1+T2+T3+T4 applied | **794** | 1025 | unchanged — all four are label/denominator changes |
| post, T3′ also applied | **765** | 1025 | −29 zero-output-evidence greens |
| post, T5 applied | **0** | 1025 | no falsifiability-backed green exists in this corpus |

Sum check at each stage: `794 = 29 + 765`; `765 + 29 + 231 = 1025`; unaccounted **0**.

---

## 3. Why the answer to "how big was the one clean drop" is 794

The owner asked for one clean drop then monotonic ratcheting. The tightenings
already *landed* cost **zero** greens between them — they relabelled and
re-denominated. The tightening not yet applied to this corpus, replacing the
stripped probe with a falsifiable one, costs **every green in it**.

That is not an argument against tightening. It is the size of the cliff, stated
before someone walks off it and reports a −1. A future loosening justified by
"the drop was only one cell" would be pointing at T1's denominator move, which
cost nothing and proves nothing.

**`ptrace` falsifiability 8/8 old → 8/8 strict (drop = 0)** is a real and
encouraging anchor, but its population is **8 cells**, not 1025. It shows the
cliff is survivable on a small set; it does not measure the cliff.

---

## 4. The published ±1 noise floor is a **net**, and it hides 7 cells

The baseline directory documents an independent replication one commit apart and
concludes *"agreement within ±1 cell of 205 per backend"*. I reproduced that
exactly by comparing `scorecard.csv` (`4c70658e7`) against
`cross-check-w14-results.csv` (`1fadc0377`) over identical row keys:

| backend | net | cells that actually changed class |
| --- | ---: | ---: |
| dbi | **+1** | **3** |
| e9patch | **−1** | **1** |
| ptrace | **+1** | **1** |
| sabre | **0** | **2** |
| liteinst | 0 | 0 |
| **total** | **+1** | **7** |

The nets match the published table cell for cell. But **7 rows changed verdict
between the two sweeps** while the net is ±1, because opposite-signed flips
cancel — `sabre` is the clearest case: two cells flipped, net zero.

The named seven: `c-programs/proc-locks` (dbi), `c-programs/setitimer-determinism`
(dbi), `c-programs/syscall-file-metadata` (dbi), `c-programs/dbi-pid-virtualization`
(e9patch **and** ptrace), `c-programs/sigmask-preemption` (sabre),
`language-runtimes/example-python-random` (sabre).

**Consequence for every future delta table, including this one:** a net-based
noise floor of ±1 cannot clear an effect smaller than 7 cells. The operational
rule *"|delta| ≤ 1 per backend is noise"* is correct for aggregates and **wrong
for attribution** — a real 5-cell regression attributable to one tightening would
sit entirely inside it. Attribution needs the **churn** floor (7), not the net
floor (1). This is why T3′'s −29 is reportable and a hypothetical −4 would not be.

---

## 5. What I did not measure, stated rather than omitted

- **The post-tightening rerun.** It is deliberately **held**: owned by
  `hermit-w14`, blocked pending #1832 remediation. Every number here is therefore
  a **reclassification of the existing baseline rows under each new rule**, not a
  fresh sweep. That is the correct method for a *definition* change — same
  measurements, different classifier — and it is the only method available
  without breaking the hold.
- **KVM** contributes 0 rows to `P` (hangs on this host, confirmed at 45 s and
  300 s). Its 130 greens on 2026-08-01 are outside `P` by construction.
- **e9patch is measurable today** and is fully represented in `P` (205 rows, 181
  green in the replication, 8 of the 29 zero-output-evidence greens). No baseline
  fix, build, or deadlock stands in its way — its exclusion from earlier sweeps
  was environmental, and it needs no special handling here.
- **T3′ vs T3** is a reading of someone else's rule text, not a measurement. I
  report both counts rather than picking one.

## Reproduction

```bash
git show origin/main:compat-envelope/pre-tightening-baseline-20260806/scorecard.csv > sc.csv
git show origin/main:compat-envelope/pre-tightening-baseline-20260806/cross-check-w14-results.csv > rs.csv
# key = (bucket, test_id, test_mode, backend); 1025 rows, identical key sets
# green = deterministic == 1 ; empty-output hash = e3b0c44298fc1c14...
```

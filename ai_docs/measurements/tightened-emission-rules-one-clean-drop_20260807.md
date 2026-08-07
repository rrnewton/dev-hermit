# The one clean drop: 1837 raw passes → 0 qualified greens

**Task:** `re-measure-every-cell-under-the-tightened-emission-rules` · **2026-08-07**
**Ratchet floor established by this document: 0 qualified greens.**

This is the single consolidated before/after the owner asked for — one clean drop, then monotonic
ratcheting. It is not a trickle of discoveries, and it is not a re-run: it re-scores the **entire
existing population** under the emission rules that have now landed.

---

## 1. The number

Produced by the gate itself (`compat-envelope/check-scorecard-tier.py` at `origin/main`), not by a
private recomputation:

| scorecard | rows | raw passes | **qualified green** |
|---|---:|---:|---:|
| `fullcorpus-scorecard.csv` | 1200 | 926 | **0** |
| `scorecard.csv` | 618 | 451 | **0** |
| `e9patch-scorecard.csv` | 454 | 454 | **0** |
| `reverie-scorecard.csv` | 12 | 6 | **0** |
| **TOTAL** | **2284** | **1837** | **0** |

Counts sum: `1200+618+454+12 = 2284` rows and `926+451+454+6 = 1837` raw passes. The gate enumerates
its inputs **by glob, not a hardcoded list**, so no scorecard can be silently omitted from the
denominator.

## 2. The definition change that produces the drop

> Green now requires `comparison_tier ∈ {full-stdout-info-stack-heap, stdout-info-stack-heap-spot-check}`.

All 2284 rows carry `comparison_tier=legacy-unqualified`. That value is an **explicit, KNOWN
non-green classification** — not a schema violation — which is why the gate exits `rc=0` rather than
REFUSED. It lets a weaker historical measurement state what it actually established instead of
leaving the field blank or being promoted into a strict tier.

**This is a re-labelling of what the existing measurements establish, not lost execution.** The cells
still ran; 1837 still pass on their raw outcome. What is zero is the count of cells whose comparison
was strict enough to *qualify*. Nothing has yet been measured with a qualifying comparator, so the
honest qualified count is zero, and every future qualifying measurement ratchets **up** from there.

## 3. Ratchet floor, per backend

`fullcorpus` = 1200 cells = 6 backends × 200 tests, no remainder.

| backend | cells | raw pass | det=1 | **qualified** |
|---|---:|---:|---:|---:|
| dbi | 200 | 156 | 156 | **0** |
| e9patch | 200 | 179 | 179 | **0** |
| kvm | 200 | 130 | 130 | **0** |
| liteinst | 200 | 118 | 118 | **0** |
| ptrace | 200 | 179 | 179 | **0** |
| sabre | 200 | 164 | 164 | **0** |
| **TOTAL** | **1200** | **926** | **926** | **0** |

## 4. Population reconciliation — every cell accounted for

Current `fullcorpus` against the 1025-row pre-tightening baseline
(`compat-envelope/pre-tightening-baseline-20260806/cross-check-w14-raw.csv`):

| bucket | cells | definition change |
|---|---:|---|
| matched (both) | 1000 | 200 tests × 5 backends |
| baseline-only | 25 | **corpus dedup**, 5 tests × 5 backends |
| current-only | 200 | **backend added** (kvm) |

`1000 + 25 = 1025` (baseline) and `1000 + 200 = 1200` (current). Both sum; no cell is unaccounted.

The 25 removed cells are **not lost coverage** — the five removed `test_id`s are all `example-`
variants: `applications/example-timed-progress-bar`, `determinism-stress/example-race`,
`language-runtimes/example-python-random`, `system-utils/example-date`,
`system-utils/example-devrand`. Zero tests were added. Effective corpus 205 → 200.

## 5. Explicitly excluded from the drop

`scorecard.csv` carries a **separate** `tier` column reading `{stripped-uncounted: 346, blank: 272}`
= 618. That is the historical claimed-bitwise **label correction**, on a different column and a
different axis from `comparison_tier`. Per the task's instruction it is **not** folded into the drop
above, and no `346/618` vs `128/133` comparison is presented — those populations differ.

## 6. Three anchors that are now stale

1. **"KVM no-result" is no longer true.** kvm reports 130 pass / 70 fail, `deterministic=1` on 130 of
   200. It is a full participant in the population, not a no-result column.
2. **"effective 205"** is now **200** (the five `example-` dedups above).
3. **"1025 rows over ptrace/dbi/sabre/e9patch/liteinst"** describes the *baseline*. Current
   `fullcorpus` is 1200 over six backends including kvm. Comparing the two head-on without §4 would be
   the population-mismatch error the task warns against.

## 7. A trap that inverts the headline

The schema carries **both** a `tier` column and a `comparison_tier` column. In `fullcorpus`, `tier` is
blank on all 1200 rows, which reads as "untiered". The authoritative gate keys on **`comparison_tier`**
(`REQUIRED = "comparison_tier"`), where all 1200 rows are `legacy-unqualified`. Reading `tier` produces
a different and wrong story. Anyone re-deriving this must use `comparison_tier`.

## 8. Scope: what this is and is not

This re-scores the whole existing population under the new rules — which is what produces one coherent
consolidated statement. It does **not** re-execute the 1200 cells. That distinction does not change the
figure: the qualified count stays 0 until a qualifying comparator is actually wired, so **the blocker
is the comparator, not the runs**. Re-running cells today would produce 1200 more
`legacy-unqualified` rows.

## 9. Reproduction

```bash
cd <dev-hermit>
git archive origin/main compat-envelope | tar -x -C /tmp/ce      # gate + scorecards together
cd /tmp/ce/compat-envelope && python3 check-scorecard-tier.py    # prints the table in §1, rc=0
# population reconciliation (§4):
#   key each row by (test_id, test_mode, backend); intersect fullcorpus-scorecard.csv with
#   pre-tightening-baseline-20260806/cross-check-w14-raw.csv; assert matched+only == each total.
```

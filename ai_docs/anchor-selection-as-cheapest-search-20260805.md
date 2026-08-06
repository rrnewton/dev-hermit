# Anchor selection as a search: the cost model, and why the current candidate set makes the search worthless

**Task:** `anchor-selection-is-a-search-pick-the-cheapest-full-green-anchor` (P0, owner)
**Date:** 2026-08-05
**Scope:** local design and analysis. **No validate run, no egress, no product change.**
All numbers measured against hermit `main` `b64d893ae9ea` and the live 585-row ledger.

---

## 0. The headline

The owner's directive has two halves. Measured, they land very differently:

> **"Make anchor choice a cheapest-estimate search"** — the search premise is confirmed:
> one candidate costs **94 ms** to evaluate against a full run costing **~1212 CPU-s**, so
> exhaustively evaluating *every* qualifying commit costs **0.8% of one full run**. No
> clever algorithm is needed; brute force is affordable.
>
> **"…pick the cheapest full-green anchor"** — on the *current* candidate set there is
> nothing to pick. **All 8 evaluable ancestor candidates, spanning d=19 to d=115, produce
> an identical result: `full`, 80 cells, 47 nodes.** The cost function is flat and maximal.
> The search is not badly designed; its feasible set is degenerate.

> **"ANY commit Z with a FULL hard green can anchor a test of X — including X's OWN
> PRE-REBASE SELF"** — *this* is where the value is, and it is blocked by a one-character
> bug. **86 of 105 qualifying commits are non-ancestors and are excluded by construction.**

---

## 1. Measured: the candidate set

Qualifying commits are those whose ledger row passes the shared qualifying-receipt
predicate (not `result == "pass"` — see `anchor_select.py` rule 2).

| | count |
| --- | --- |
| qualifying ledger rows | 107 |
| distinct qualifying commits | 105 |
| **ancestors of hermit HEAD** | **11** |
| **NON-ancestors** | **86** |
| not present in the local repo | 8 |

Ancestor candidates by first-parent distance to HEAD:
**19, 38, 53, 54, 60, 61, 108, 110, 111, 112, 115.**

Note the nearest qualifying anchor is already **19 commits back**. There is no near anchor
to be had.

## 2. Measured: the cost function over that set is flat

Driving the real selector (`git diff --name-only <anchor>..HEAD | ci/select-tests.rs`):

| anchor | distance | decision | cells | nodes | files in diff |
| --- | --- | --- | --- | --- | --- |
| `d53550510d1e` | 19 | full | 80 | 47 | 123 |
| `2a01963e6121` | 38 | full | 80 | 47 | 141 |
| `e11175c9de88` | 53 | full | 80 | 47 | 155 |
| `ddfcbf773cb2` | 54 | full | 80 | 47 | 156 |
| `b4e94ce4455d` | 60 | full | 80 | 47 | 159 |
| `1b12bc1a9f2a` | 61 | full | 80 | 47 | 159 |
| `e8a0d8d3be3b` | 108 | full | 80 | 47 | 184 |
| `9ebe1608303c` | 115 | full | 80 | 47 | 187 |

The file count grows with distance; **the cost does not**. Cause is the known
`force_full` monotonicity: once any commit in the window touches a force-full path, the
cumulative diff forces full at every greater distance. Commit #1 after the nearest anchor
already does.

**Consequence for the search design:** among ancestors along one chain, cost is monotone
non-decreasing in distance, so *nearest is provably argmin* — and here argmin is the
maximum possible cost. The existing `anchor_select.py` already picks nearest
(`:403-407`), which is the right rule for that subset. **Searching harder over ancestors
cannot help.** Only widening the candidate set can.

## 3. The cost model

The right currency is **CPU-seconds**, not wall: CPU is additive across nodes,
load-immune, and the history store measures it well (p50/p95/max per node), whereas it
stores only *max* wall.

```
cost(anchor) = Σ  p50_cpu[n]   for n in select(diff(anchor..tip))
```

**Validated against measurement:** Σ p50_cpu over the 47 portable nodes = **1212 s**
against a measured median full-run CPU of **1378 s** (ledger, n=154) — **0.88×**. The 12%
gap is plausibly the privileged lane plus harness overhead outside the portable node set.
That is a well-calibrated additive model, not a guess.

For wall reporting (never for the argmin), makespan is bounded below by the
dependency-respecting critical path. Computed over the 47-node portable DAG (77 declared
edges) using per-node `max_wall`: total work 9505 s, **critical path 1243 s**, intrinsic
parallelism ceiling 7.6×. *Caveat:* that critical path uses `max_wall` per node, which
compounds worst cases — it exceeds the measured median full run (414 s quiet / 528 s
blended), so it is an upper bound, not a prediction. **The store has no p50 wall per
node**; adding it to the deriver is the prerequisite for a wall-denominated cost model.

Where saving actually lives — top CPU contributors (p50):

| node | p50 CPU |
| --- | --- |
| `build.runtime_release` | 253.0 s |
| `build.workspace` | 162.2 s |
| `test.app_strict_verify` | 94.5 s |
| `test.strict_compat` | 86.0 s |
| `test.hermit_integration` | 74.2 s |

The top two alone are **415 s = 34%** of the total. A selection is only worth having if it
excludes them; a "selective" run that still builds the workspace and the runtime saves
little. This should be the headline metric of any selection, not the node count.

## 4. The search

```
candidates = qualifying_receipt_rows()                  # NOT result=="pass"
           |> filter(receipt at/above gate floor)
           |> filter(commit present locally)
           |> filter(selection_mode == "full")          # one hop; never chain
best = argmin over candidates of cost(anchor)
       subject to  diff(anchor..tip) being well-defined (see §5)
tie-break: prefer an ancestor; then the newer finished_at; then the shorter distance
```

**Pruning (sound, from monotonicity):** ancestors on the tip's first-parent chain need not
all be evaluated — evaluate the nearest; if it decides `full`, every farther ancestor on
that chain also decides `full` and can be skipped. That is the entire ancestor subset
collapsed to one evaluation.

**But do not build the pruning first.** Measured: `git diff` 64.6 ms + selector 29.6 ms =
**94.2 ms per candidate**; all 105 candidates = **9.9 s ≈ 0.8% of one full run's CPU**.
Exhaustive evaluation is already free. Implement brute force, keep the monotonicity fact
as a documented explanation of the shape rather than as an optimisation.

**Early exit:** cost 0 (empty diff ⇒ selector `skip`) is the global minimum and can stop
the search immediately. This is the clean-rebase case in §5.

## 5. What unblocks the search: two-dot diff for non-ancestor anchors

The owner's "any commit Z … including X's own pre-rebase self" requires non-ancestor
anchors. Today they are unusable, for a specific reason:

`hermit/ci/select-tests.rs:636` computes `git diff --name-only {baseline}...HEAD` —
**three dots**. For a non-ancestor baseline that silently relocates the effective anchor
to `merge-base(baseline, HEAD)`, a commit that carries **no receipt at all**. So the run
would inherit green from something nothing validated. `anchor_select.py` responds by
*requiring* ancestry (its rule 3), which is correct as a safety measure and is exactly
what excludes the 86 non-ancestors.

**The fix is two-dot (`{baseline}..HEAD`), not relaxing the receipt rule.** Two-dot gives
the true tree delta between the anchor and the tip, which is the quantity the footprint
map needs and which is well-defined for any pair.

### Be precise about when the pre-rebase self is actually cheap

It is not automatically cheap. If `Z` = pre-rebase PR head (on main@old) and `X` =
post-rebase head (on main@new), then `diff(Z..X)` contains **all the main drift the rebase
absorbed**, plus any conflict resolutions. That can be large.

The genuinely near-zero case is the **clean rebase over little drift**, where the tree
delta is empty or tiny — the sibling `soft-inherited-validation-across-clean-rebase` case.
Then the diff is empty, the selector returns `skip`, and cost is 0.

This is precisely why the owner is right to call it a **search**: cheapness is not monotone
in distance, not monotone in recency, and not predictable from which branch a candidate
sits on. The only way to know which of 105 candidates is cheapest is to evaluate them —
and at 94 ms each, you can.

## 6. Verify bar

Anchor choice is a green-inheritance authority, so both directions must be bracketed:

- **Negative (safety).** A candidate whose receipt does not qualify, is below the gate
  floor, or is `selection_mode == selective` must be **refused as an anchor** — state the
  refusal count. The existing 69%-fake-anchor measurement (346 rows pass a bare
  `result == "pass"` filter, only 107 qualify) is the standing evidence for why.
- **Negative (soundness of the widening).** With a non-ancestor anchor, assert the diff is
  computed **two-dot**: plant a non-ancestor baseline and confirm the effective diff base
  is the baseline itself and not `merge-base`. Without this the widening is unsafe.
- **Positive (non-vacuity).** A candidate that *does* qualify must be **selected** and must
  produce a strictly cheaper selection than full on at least one real case — otherwise the
  search reports "no anchor" forever and looks safe while doing nothing. Today no
  candidate satisfies this (§2), which is itself the finding.
- **Cost-model calibration.** Re-check Σ p50_cpu against measured run CPU periodically;
  0.88× today. A model that drifts silently turns argmin into a coin flip.

## 7. Recommendation, ordered

1. **Fix the three-dot to two-dot** in `select-tests.rs:636`, with a planted-non-ancestor
   bracket. This is the one change that turns 86 dead candidates into live ones and is
   prerequisite to everything else here.
2. **Add the empty-diff fast path** (clean rebase ⇒ `skip` ⇒ cost 0), the only case
   currently known to save anything.
3. **Widen `anchor_select.py`'s candidate set** past ancestors once (1) lands, replacing
   nearest-ancestor with argmin-cost over the full qualifying corpus. Brute force.
4. **Report cost in CPU-seconds and name the excluded heavy nodes**, not the node count.
5. **Add p50 wall per node** to `node-cpu-budgets` if a wall-denominated cost is wanted.
6. Do **not** invest in search cleverness. At 0.8% of a full run for exhaustive evaluation,
   the algorithm is not the bottleneck; the candidate set and the diff semantics are.

## 8. Limitations

- **No validate run.** Costs are modelled from the history store, not observed by running
  the selections.
- The 8 evaluated ancestors are those resolvable in the local repo; 3 of the 11 were not
  evaluated, and 8 qualifying commits are absent locally entirely. Since all 8 evaluated
  produced `full` and the cause is monotone, the remaining 3 are expected to be `full` too
  — expected, not verified.
- `cost = Σ p50_cpu` ignores per-node variance and cache state; a cold-cache
  `build.workspace` costs far more than its p50. The model ranks candidates, it does not
  predict a run.
- The critical-path figure uses `max_wall` and is an upper bound (it exceeds the measured
  median full run). Do not quote 1243 s as an expected wall.
- The pre-rebase-self analysis is reasoning about `diff` semantics; I did not have a live
  pre/post-rebase PR pair with two qualifying receipts to measure.
- `anchor_select.py` is another agent's untracked work-in-progress. I read it and designed
  around it; I did not modify it.

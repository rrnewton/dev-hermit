# Outer DAG width: where it stops helping and starts COSTING (not just the 4.24x ceiling)

Task `parallelism-is-two-level-outer-dag-times-inner-step-width` (owner). 2026-08-04,
hermit-220 (opus-4.8). Answered from EXISTING data + DAG arithmetic — **no new
measurement** (box was contended by ghdag's live curve-3 fan-out; measuring would corrupt
both). All figures LABELLED OUTER vs INNER, the error being corrected.

## The distinction the owner drew

`TOTAL_WORK 5360s / CRITICAL_PATH 1265s = 4.24x` is the **dep-only graph-shape ceiling** —
what the DAG *could* absorb with infinite resources. It says nothing about whether adding
outer workers *helps*. New evidence (#1592: `-j4`=492s PASS vs 589s higher width, `-j16`
FAIL) says outer width is **negative above ~4**. That is a different question.

## OUTER width vs makespan (list-scheduling sim, honors deps + resource_caps + -j)

`hermit/ci/dag/portable.json`, 47 nodes, `resource_caps={hermit_guest:1, manifest_guest:4}`:

| outer -j | makespan | speedup | peak co-sched mem (rss / hardcap-sum) |
|---:|---:|---:|---:|
| 1 | 5360s | 1.00× | 12 / 24 GiB |
| 2 | 3355s | 1.60× | 16 / 32 GiB |
| **4** | **3005s** | **1.78×** | 25 / 50 GiB |
| 5 | 3005s | 1.78× | 26 / 54 GiB |
| 8 | 3005s | 1.78× | 31 / 58 GiB |
| 16 | 3005s | 1.78× | 36 / 72 GiB |
| 32 | 3005s | 1.78× | 36 / 72 GiB |

**Outer -j stops helping at j=4** — dead flat 1.78× thereafter. Reproduces the "flat at
j≥5" sweep and the WARM empirical plateau (ledger: 167 warm runs, parallelism med **1.78×**).

**Why flat:** `hermit_guest=1` serializes the 16 single-threaded (`--test-threads=1`) guest
test nodes into a ~3005s spine. Extra outer workers have nothing to run. The **real lever
is the cap, not -j**: cap 1→2 = 3.13×, cap 1→4 = 4.24× (hits the dep ceiling), >4 nothing.
The cap is PMU/vfork/timeslice-flake gated (audit F2, big-box A/B).

## Why outer width past ~4 actively COSTS (net negative)

Upside is provably 0 past j4. Downsides, evidence-ranked:

1. **Load-dependent flaky FAILURES rise with concurrency** (strongest). The warm tail is
   concurrent hermit guest nodes; running more at once raises effective load → PMU-skid /
   vfork-timeout / timeslice-under-load nondeterministic failures. Empirical full-run pass
   rate is only **43% (97/228)**; the `-j16` FAIL is this class. "Narrow = greener."
2. **Contention slowdown makes wide actively slower** — measured 492s@j4 → 589s higher
   width (+20%). The idealized sim can't show this (no reclaim/oversubscription penalty);
   the empirical walls do.
3. **Per-node cgroup OOM when inner `CARGO_BUILD_JOBS` is unpinned** — #1592 "lacks the
   pin"; a build node blows its own 8.5 GiB cap. NOT a full-box memory problem (box=754 GiB,
   ~443 GiB free; peak co-scheduled hardcap-sum at j16 is only 72 GiB).

## Cache-state dependence (don't quote one number)

- **WARM** (typical drain re-validate): spine-bound, outer -j useless past ~4, higher only
  adds flaky-fail + contention → run **narrow**.
- **COLD** (first build): cargo builds fan out → benefits to ~j8 (audit saw 9.12×). Outer -j
  IS worth it cold.

## DRAIN recommendation (23 heads)

Run each validate **narrow (-j4)** — faster AND greener per run (matches owner's evidence).
Fleet throughput comes from **many concurrent narrow validates** (drain is slot-bound), NOT
from widening a single validate. Do not raise per-validate -j; if you want a single validate
faster, the lever is the `hermit_guest` cap (gated), not -j.

## Provenance / reproduction

- Sim + ledger parse: inline python over `hermit/ci/dag/portable.json` +
  `ignored/validate-run-ledger.jsonl` (228 full runs). `hard_mem_max_bytes` and
  `resources.hermit_guest` live under `hint`, not step top-level (parse trap).
- Prior: `experiments/validate_parallelism_audit_20260803/` (cold 9.12× / warm 1.58×,
  cap4/j8 sim = 5.55×), 08:42 task note (INNER width per node class).
- Sibling curve-1: `experiments/cargo_build_jobs_speedup_20260802/README.md`.

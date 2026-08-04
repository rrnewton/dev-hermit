# validate scheduler-algorithm bakeoff

**Task:** `validate-scheduler-algorithm-bakeoff` (P0, owner overnight brief:
"We've even tested validate with the different dag runner scheduling algorithms
to find the best.") Owner agent: hermit-220.

**Host:** devbig014, AMD EPYC 9D85 158-Core ×2 = 316 cores. Date 2026-08-04.
**DAG:** `hermit/ci/dag/portable.json` (47 nodes) — the portable validate gate.

## What "scheduling algorithm" means here (read the runner, did not assume)

`safe-ci-dag-runner` supports **exactly three dispatch planners** (`--planner`,
`Planner` enum). Enumerated by reading the runner source, not guessed:

1. **greedy-lpt** (default) — launch the ready step with the largest single
   `est_duration` first (longest-processing-time-first list scheduling).
2. **critical-path** — launch the ready step with the largest `bottom_level`
   first (longest *remaining* est-weighted path = critical-path list
   scheduling).
3. **cpa** — two-phase moldable allocator (Radulescu & van Gemund 2001):
   choose each step's inner `-j` width by balancing the critical path against
   per-core area over measured speedup curves, then critical-path
   list-schedule at those widths. This is the only planner that changes WIDTH.

There is **no FIFO planner**; with equal estimates greedy-lpt degenerates to
registration order.

### Runner-version caveat (load-bearing)

The runner hermit's CI actually invokes via `ci/run-dag.sh` is the **vendored
`hermit/agent-utils` at v0.2.0 (84580db), which has NO `--planner` flag at all**
(`run-dag.sh portable --planner X` → "unrecognized arguments"). The three
planners exist only in the newer agent-utils **v0.12.0** (parent checkout /
scratch branch). This bakeoff therefore drives the v0.12.0 runner directly
against `hermit/ci/dag/portable.json`, replicating `run-dag.sh`'s invocation
(cwd = hermit root; `run --dag ci/dag/portable.json --planner P`). **Finding for
the owner: choosing a planner in production first requires bumping hermit's
vendored agent-utils pin off v0.2.0 — today the gate has no planner choice.**

## Structural result (modeled from the plans — decisive, planner-invariant wall)

`plan --planner P` for all three (raw output in `ignored/plans/`):

| planner | critical path | crit-path len | dispatch order |
|---|---|---|---|
| greedy-lpt | e2e.metadata → build.workspace → lint.clippy → test.strict_compat | **1265.0 s** | order A (front-loads longest single node: test.strict_compat slot first) |
| critical-path | *(identical)* | **1265.0 s** | order B (front-loads the actual chain: e2e.metadata→build.workspace→lint.clippy) |
| cpa | *(identical)* | **1265.0 s** | order B (**byte-identical to critical-path**) |

- **All three planners produce the IDENTICAL critical path = 1265.0 s.** The
  dispatch ORDER genuinely differs (greedy-lpt hash ≠ critical-path/cpa hash;
  critical-path ≡ cpa), so the planners are really being exercised — but the
  order change cannot move wall.
- **Why order is wall-neutral here:** the gate is a 4-node dependency CHAIN
  (e2e.metadata → build.workspace → lint.clippy → test.strict_compat). With 316
  cores the entire 47-node fan-out fits with slack — cpa reports `area/P =
  16.96 s` of per-core work against a `1265 s` critical path, i.e. the DAG is
  **chain-bound, not area/contention-bound**. No ready step ever waits for a
  core, so which ready step launches first is cosmetic.
- **cpa cannot help either:** `allocator (cpa): knee-exhausted; P=316 cores;
  critical-path=1265.0s, area/P=16.96s, lower-bound=1265.0s,
  modeled-makespan=3005.0s.` Widening steps can't beat a chain lower bound.

This matches hermit-226's independent finding (`484s gate is a short serial
chain of long nodes; width can't shorten a chain`). The lever that shortens wall
is **pruning the critical-path chain** (`prune-artificial-deps-on-validate-critical-path`,
hermit-226), NOT choosing a planner.

## Empirical wall confirmation (warm, medians) — MEASURED

Method: v0.11.0 runner (parent `agent-utils`), warm primary hermit target, `-k`
(keep-going), `--perf-dir` per run, one planner at a time, alternating order per
round to cancel warm-drift. **WALL CLOCK reported; CPU/wall shown only with its
components (total CPU s = user+sys, wall s) so the derived ratio is checkable.**
Medians over N runs. Driver: `ignored/run-bakeoff-v2.sh`; raw `results.csv`.

**Valid-comparison basis (load-bearing):** the earlier attempt ran the FULL DAG
and hit fail-fast on sandbox-blocked nodes, stopping at a *different node per
planner* (greedy reached 38, critical-path 33, cpa 3) — those walls are NOT
comparable and are archived in `results-failfast-INVALID.csv`. This campaign runs
a **verified all-pass 20-node subset** (`--only …`) so every run completes
`n_failed=0` and `wall_s` is a real makespan, apples-to-apples across planners.

| planner | median wall | wall runs (all-pass) | median CPU s | median CPU/wall | median observed cores | resolved outer jobs | N |
|---|---|---|---|---|---|---|---|
| **greedy-lpt** (default) | **67.8 s** | 65.8, 66.9, 67.8, 69.6, 70.8 | 239.6 | 3.47 | ~39 / 316 | 316 | 5 |
| **critical-path** | **69.4 s** | 67.7, 68.8, 70.0, 71.0 | 243.0 | 3.46 | ~40 / 316 | 316 | 4* |
| **cpa** | **FAILS** | — (3/3 runs error at 2 s) | — | — | — | 316 | 3 |

\* critical-path round 3 was a contaminated outlier (wall 113.5 s, 1 node failed,
box busy% spiked to 54.9% = a concurrent-load collision from another agent) —
excluded on `n_failed≠0`. That single dirty sample is exactly why medians over N
runs, not single samples, are required.

### Findings

1. **Planner choice is WALL-NEUTRAL on this gate.** greedy-lpt 67.8 s vs
   critical-path 69.4 s = a 1.6 s (~2.4%) gap, well inside the greedy run-to-run
   spread (65.8–70.8 s = 5 s). The measurement confirms the modeled prediction:
   the gate is a dependency **chain**, so reordering the ready fan-out cannot move
   wall. **Recommendation: keep the default `greedy-lpt`;** switching planners buys
   nothing here. The wall lever is chain-pruning
   (`prune-artificial-deps-on-validate-critical-path`), not planner selection.

2. **CPU/wall is ~3.5×, NOT ~21.8×.** Observed CPU/wall medians are 3.47/3.46 with
   observed concurrency only **~40 of 316 cores busy** (`total_busy_pct` 12–15%).
   The 21.8× figure was an ARITHMETIC INFERENCE (host_cpus/8 → -j16 assumed fully
   engaged), never an observation; a real full run reported ~2.2×. This all-pass
   subset independently lands in the same low-single-digit regime. The gate is
   latency/chain-bound — most of the box sits idle regardless of configured width,
   which is why the configured -j and the observed concurrency diverge and why
   CPU/wall stays low. Every run above **emits the resolved outer width (316) and
   observed busy cores**, not a configured value.

3. **cpa is UNUSABLE on `portable.json` as authored (real defect, not sandbox).**
   All 3 cpa runs fail in ~2 s with `bash: -c: syntax error near unexpected token
   '-j'` — cpa assigns an inner `-j` width and substitutes it into steps whose
   commands cannot take one (e.g. the `cargo-nextest available` check,
   `lint.rustfmt`, `e2e.metadata`). greedy-lpt and critical-path never change
   width, so they are immune. cpa would require per-step `jobs_flag` correctness
   that `portable.json` does not provide — and even if fixed, its own modeled
   lower bound equals the critical path (1265 s), so it cannot beat a chain.

### Per-process (per-step) memory + concurrency — WARM

Full table in `per-step-memory-WARM.csv` (from clean greedy round 5). Peak RSS is
captured **per step = per process group**, plus per-step `effective_cores` and
`oom_kills`. Top consumers:

| step | peak RSS | observed cores | oom |
|---|---|---|---|
| test.hermit_integration | 2.38 GiB | 1.9 | 0 |
| build.workspace | 1.35 GiB | 2.8 | 0 |
| doc.doctests | 1.23 GiB | 1.3 | 0 |
| (17 others) | ≤ 183 MiB | ≤ 1.1 | 0 |

- **Max single-process peak = 2.38 GiB; sum-of-all-peaks (worst case, all peak
  together) = 6.05 GiB; zero OOM kills** in every clean run.
- **CAVEAT (cold vs warm):** `build.dbi_release` shows only 44.7 MiB here because
  it is a **warm no-op** (1.3 s, "Finished in 1.14 s" — no rebuild). The 8 GiB
  `hard_mem_max_bytes` OOM that has blocked landing is a **COLD** dbi build where
  `THIRD_PARTY_BUILD_JOBS = min(nproc,32)` scales the job count with the machine
  while the cap stays fixed. This warm bakeoff does **not** exercise that path;
  the per-process cold-build memory model belongs to the per-step sweep study
  (`per-step-parallel-speedup-study-and-j-model`), which measured the cold build.

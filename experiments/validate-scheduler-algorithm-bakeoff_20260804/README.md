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

## Empirical wall confirmation (warm, medians)

*(measured section — filled from the timed campaign; see `results.csv`)*

Method: v0.12.0 runner, warm primary hermit target, `-k` (keep-going),
`--perf-dir` per run, one planner at a time (no concurrent runs — concurrency
would pollute wall). **WALL CLOCK reported, not CPU/wall.** Medians over N runs.
Some nodes fail under the agent BpfJailer sandbox (DynamoRIO cmake block on
DBI/third-party-backends, integration tests); failures are fast and identical
across planners, so the planner A/B/C wall comparison stays apples-to-apples,
but the absolute wall is a partial-gate wall, stated as such.

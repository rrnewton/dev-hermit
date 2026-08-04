# DAG memory caps — derived AS A SET, not reactively (2026-08-04)

## Question
The safe-ci-dag-runner enforces a per-node `hard_mem_max_bytes` as the inner cgroup
`memory.max` (the OOM enforcer). Task `derive-all-dag-memory-caps-as-a-set-not-reactively`:
were these 54 caps derived in relation to one another, or hand-picked round constants
so that fixing the node that OOMs merely MIGRATES the OOM to the next-tightest node?

## Method
- Enumerated all 54 nodes + `hint.hard_mem_max_bytes` / `rss_baseline_bytes` from
  `hermit/ci/dag/{portable,privileged}.json` (`synthesize_set.py`).
- Joined every available cgroup-RECORDED `memory.peak`, each carrying its measurement
  CONDITION `{j, warm/cold, lane, source}` (the "carry the condition with the value"
  rule): 10 from `dag-mem-caps-pinned-jobs_20260804/results.csv` (j8 warm-shared),
  5 from the codex/238b task note (j1 serial), 2 OOM floors (rr, strict_compat).
- Classified each node compile-bearing (spawns rustc/cc1plus fan-out that scales peak
  with -j) vs not. Computed tightness = measured_peak / hard_cap.

## Results (see set-table.txt)
- 18/54 measured, 36 unmeasured. **20 of the unmeasured are compile-bearing = the
  latent OOM chain.** #1583 → #1597 → strict_compat is nodes 1–3 of that chain.
- Two failure modes coexist: BINDING/OOM (rr, strict_compat @ ratio 1.00) and SLACK
  (doc.doctests 0.10, build.flaky_harnesses 0.07, light nodes 0.00–0.14, >75% waste).

## Interpretation
Caps are NOT a set — they are per-node round guesses. Two consequences:
1. **OOM migration (too-tight compile nodes).** Patching one cap moves the OOM to the
   next-tightest compile node; 20 remain unmeasured. The convergent fix is SYSTEMATIC,
   not per-node: pin `CARGO_BUILD_JOBS` on EVERY compile-bearing node (or have the DAG
   runner default-cap job counts for them), because a memory cap and a job count are
   the same knob viewed twice — `NUM_JOBS=nproc(~284)` leaking from the CPU quota is
   what blows the cap.
2. **Wasted admission (too-loose light nodes).** In an admission-limited drain, a node
   pinned at 10–25× its real peak needlessly consumes admission budget, lowering
   achievable parallelism. Right-sizing the SLACK family reclaims concurrency.

Every re-derived cap must be recorded as `{j: N, bytes: M}` with a smaller-cap negative
control that plateaus (rules out an unbounded leak vs a raised ceiling).

## Reproduction
`python3 synthesize_set.py` (reads ../../hermit/ci/dag/*.json + inline measured inputs).

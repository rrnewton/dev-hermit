# Hermit portable CI DAG — profiling & parallel-utilization analysis

**Date:** 2026-08-02 · **Agent:** hermit-ci · **Lane:** `CI (GitHub-managed portable)`
· **DAG:** `hermit/ci/dag/portable.json` (46 steps)

## Question

Where does the portable-CI wall-time go, and is `safe-ci-dag-runner`'s available
parallelism actually being exploited? This feeds the `ci-dag-parallelize`
refactor.

## Method

- Ground-truth measurements come from the real hosted run's `dag-perf-regular`
  artifact at Hermit `main` SHA `c7531a83` (`summary.csv` + `step_profiles.csv`
  here). 4-core GitHub-managed runner, `ci/run-dag.sh portable --max-mem 14G`
  (no `-j` → outer concurrency 1).
- `analyze-dag.rs` (rust-script) joins `portable.json` deps with the real
  per-node `elapsed_s`, computes the weighted critical path (longest-path DFS
  over deps), total node-work, ideal speedup, and emits `portable-dag.dot`.

Reproduce:

```bash
rust-script analyze-dag.rs <path-to>/ci/dag/portable.json step_profiles.csv portable-dag.dot
dot -Tsvg portable-dag.dot -o portable-dag.svg   # optional local render (SVG not committed)
```

## Results

| metric | value |
| --- | --- |
| nodes | 46 |
| total node-work (Σ real elapsed) | **1918s** (32.0 min) |
| **critical path** | **935s** (15.6 min) |
| ideal DAG speedup (work / critpath) | **2.05×** |
| **measured wall** | **1920s** (32.0 min) |
| runner cores / outer jobs | 4 / **1** |
| total CPU busy % | 68.6% (user 4698.6s + sys 554.4s over 4 cores) |

### The headline finding

**measured wall (1920s) ≈ total node-work (1918s)** ⇒ the portable DAG runs its
**nodes essentially serially** (`outer_jobs=1`). The 2.4–2.7× core usage is
entirely *within-node* cargo/nextest `-j`, **not** DAG-level fan-out.
`safe-ci-dag-runner`'s node parallelism is not exploited on this lane.

### Critical path (bold red in the graph)

```
build.workspace              240.9s
  └─ build.dbi_release       563.9s   ← #1 long pole (release rebuild)
       └─ build.liteinst_runtime_release  78.7s
            └─ test.liteinst_strict        51.8s
= 935s
```

### Top nodes by real elapsed

| s | node |
| --- | --- |
| 563.9 | build.dbi_release |
| 240.9 | build.workspace |
| 235.0 | test.strict_compat |
| 78.7 | build.liteinst_runtime_release |
| 78.1 | test.hermit_integration |
| 69.9 | test.cli |
| 68.4 | build.sabre_release |
| 51.8 | test.liteinst_strict |

E2E `manifest_*` cells total only ~180s and are **not** the portable-lane
bottleneck (the `#1447` fan-out already parallelized the cheap part).

## Interpretation → refactor levers (`ci-dag-parallelize`)

1. **Enable node-level outer parallelism.** Wall is ~2× the critical path;
   scheduling toward the 935s critpath is a ~2× win. Gated by the 14G mem cap
   vs GB-scale build nodes → needs bigger runners or memory-aware co-scheduling.
2. **Attack `build.dbi_release` (563.9s).** Full release rebuild after the debug
   workspace build is the single largest cost. Cache/reuse artifacts.
3. **Split the serial build chain + heavy test nodes** (`strict_compat` 235s,
   `hermit_integration` 78s, `cli` 70s) into parallel per-runner jobs that each
   download a build-once artifact — extend the `#1447` model from e2e cells to
   builds + heavy tests.

## Shareable renders

- Rendered graph (Graphviz): https://www.internalfb.com/intern/graphviz/?paste=2445698597
- Raw DOT source paste: https://www.internalfb.com/intern/paste/P2445698602/
- DOT source (this dir): `portable-dag.dot`

Note: the rendered SVG is generated media and is intentionally not committed
(binary/generated-media policy); regenerate locally from `portable-dag.dot`.

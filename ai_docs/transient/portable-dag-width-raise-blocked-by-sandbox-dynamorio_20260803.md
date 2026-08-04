# Portable DAG width raise: parallelizes, but a clean wide wall is unmeasurable in the 3pai sandbox

Author: hermit-226 (coordinator, opus-4.8) · 2026-08-03 · devbig014 (AMD EPYC 9D85, 316 cores, 754 GiB)
Slot: worktrees/226v/hermit @ codex/validate-result-cache-by-sha (35ce59f3), tree clean.
Task: decide whether to raise `CI_DAG_JOBS` default (currently 2) for the portable validate lane.

## TL;DR

- **The portable DAG genuinely parallelizes; it is NOT one-node-bound.** Measured: with a
  working DBI cache, 18 nodes ran concurrently — 596.3s of summed node work compressed into a
  128.3s wall = **4.6x realized parallelism** (lower bound; 26 nodes were skipped). Longest single
  node `e2e.manifest_language_runtimes` = 106.2s; serial-spine root `build.workspace` = 3.3s cached.
- **A clean full-green `-j16` wall CANNOT be obtained in the 3pai agent sandbox**, so the
  "484s@-j2 vs wide" comparison the task asked for is not completable here. Reason: every DBI/
  DynamoRIO-building node fails in-sandbox, for environment reasons, not width and not product bugs.
- **The predecessor's attribution ("build.dbi_release concurrency race = the real reason width
  was 2", category b) is REFUTED / re-attributed.** The failures are (1) reflink-seeded
  `CMakeCache.txt` holding the *primary* checkout's absolute path, and (2) the sandbox breaking
  DynamoRIO's dependency-file generation — reproduced even in a single-node build.
- **Do NOT raise `CI_DAG_JOBS` default based on sandbox measurements.** Finish the wall on a real
  CI runner. Separately, file the DynamoRIO-built-4x redundancy as a real design win.

## What was measured (solid, sandbox-independent)

Run: `CI_DAG_JOBS=16 ./ci/run-dag.sh portable -j 16 -k --perf-dir <d> -v` (keep-going), reflink-
seeded cache present (as slot was provisioned).

| metric | value |
|---|---|
| passing nodes (ran to green) | 18 |
| longest passing node | `e2e.manifest_language_runtimes` 106.2s |
| next heaviest | manifest_determinism_stress 80.5, regular_crates 78.2, clippy 73.4, bin_c/backend_parity_c 71.6 |
| sum of passing-node elapsed | 596.3s |
| wall (partial run) | 128.3s |
| realized parallelism (sum/wall) | **4.6x** |
| serial-spine root `build.workspace` (cached) | 3.3s |
| nodes that ran / total | 21 / 47 (26 skipped as DBI-dependent) |

The six 70-106s `e2e.*` nodes consumed the *cached* DBI and ran concurrently — this is the direct
evidence the fan-out parallelizes. The full-green wide wall is **unmeasured**: the 26 skipped nodes
are exactly the DBI-dependent backend-parity/verify nodes, which are the expensive ones, so the wall
and its ratio vs 484s cannot be honestly estimated from this partial run.

## Why the wide run cannot go green in the sandbox (three reproductions)

1. **Reflink-seeded CMakeCache pollution (deterministic).** `build.dbi_release` failed with:
   `CMake Error: The current CMakeCache.txt directory .../worktrees/226v/hermit/target/install-build/
   dbi-client/CMakeCache.txt is different than the directory .../hermit/target/install-build/dbi-client
   where CMakeCache.txt was created.` The slot's `CMakeCache.txt` had
   `CMAKE_CACHEFILE_DIR:INTERNAL=/home/newton/work/dev-hermit/hermit/...` — the PRIMARY checkout path
   (6 absolute refs to the primary). CMake stores absolute paths; a reflink copy from the primary
   poisons them. (memory: reflink-seed-cmake-cache-cross-worktree-pollution — "exclude install/dbi".)

2. **Sandbox EPERM on `.o.d` writes under concurrent compile (`-j16`).**
   `pt_section_file.c:255:1: fatal error: opening dependency file
   CMakeFiles/ipt.dir/.../pt_section_file.c.o.d: Operation not permitted`. Single-threaded writes to
   those same paths succeed, so it is BpfJailer denying FILE_OPEN under concurrent pressure.

3. **Sandbox abort in DynamoRIO's dependency scan even single-node.** Running just
   `cargo build --workspace --features third-party-backends` alone (no competing DAG nodes):
   `gmake[2]: *** [.../ipt.dir/depend] Aborted (core dumped)` → `reverie-dbi/build.rs:339 failed to
   build and install DynamoRIO: exit status 2`. No EPERM this time — the dependency-scan tool itself
   crashes. Same failure class (DynamoRIO `.o.d` generation), reproduced with zero cross-node
   concurrency. (memory: validate-env-sandbox-block-classification — BPFJailer+DynamoRIO.)

Conclusion: in the 3pai sandbox (`3pai_sandbox.slice`, META_3PAI_* present) DynamoRIO cannot be
built from scratch. The DBI nodes can only run against a DynamoRIO build provisioned OUTSIDE the
sandbox — and the reflink path to provide it poisons the install-build CMakeCache.

## Design finding worth acting on

**Four separate portable nodes each independently build reverie-dbi/DynamoRIO:** `build.workspace`,
`build.dbi_release`, `test.hermit_integration`, `test.rr_suite_contract`. That is redundant heavy
work AND the entire source of the width fragility (concurrent DynamoRIO builds collide; each is
memory-heavy — `build.workspace` hint: rss_baseline 5 GiB, hard_mem_max 8 GiB). A single
`build.dynamorio` spine node built once, with all DBI consumers depending on it, would (a) cut
redundant work, (b) remove the concurrent-build collision, and (c) make a wide raise safe.

## Recommendation

1. **Keep `CI_DAG_JOBS` default at 2 for now.** The parallelism benefit is real but the wide wall is
   unproven; raising it on sandbox evidence would ship an unvalidated change (and the DBI nodes are
   memory-heavy — a naive raise risks memory oversubscription).
2. **Measure the clean wide wall on a real CI runner** (non-sandbox; DynamoRIO builds; clean cache).
   Only then compare to 484s@-j2 and choose a width.
3. **Coordinate the width value with hermit-220 (admission control).** Per-node memory reservation
   must serialize/gate the DynamoRIO build node; unbounded `-j` fights admission. (memory:
   validate-box-resource-footprint — reserve ~2 GiB/box, memory binds admission not cores.)
4. **File the single `build.dynamorio` spine-node refactor** as the real win + fragility fix.

## Traps corrected from the inherited handoff

- Handoff "TRAP 2: step_profiles CSV is TAB-separated" is WRONG for this build: data rows are
  COMMA-separated, 18 fields (verified with `cat -A`: no tab bytes). Parse `-F,`, strip trailing CR.
  Columns: 9=step, 12=elapsed_s, 13=returncode, 14=ok.
- Handoff "the failing node is build.dbi_release (one concurrency-race node)": there were 3 failures,
  all one root cause (env), and the root cause is cache/sandbox, not `-j16` product concurrency.

## Reproduction

```
# in a NON-sandbox runner with a clean cache:
CI_DAG_JOBS=16 ./ci/run-dag.sh portable -j 16 -k --perf-dir <d> -v
# in-sandbox failure repro (any of):
rm -rf target/install-build target/*/build/reverie-dbi-* target/*/build/hermit-install-*
cargo build --workspace --features third-party-backends   # -> DynamoRIO depend Aborted / EPERM
```

Slot note: `worktrees/226v` `target/` DBI cache was cleared during this investigation and cannot be
rebuilt in-sandbox; DBI work in this slot needs a DynamoRIO cache reseed from outside the sandbox.

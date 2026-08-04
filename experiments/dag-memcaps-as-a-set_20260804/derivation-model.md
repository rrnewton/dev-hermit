# DAG memory caps — the derivation MODEL (cap = f(cores)), 2026-08-04

Companion to `README.md` / `set-table.txt`. The README established *that* the 54 caps are
per-node round guesses (not a set). This file states the owner's **derivation method** so that
once the prerequisite lands, every cap computes mechanically instead of being re-guessed.

## Three numbers per node — NEVER conflate them
For every node report, separately:
1. **observed_peak** — cgroup-recorded `memory.peak`, WITH its condition `{j, warm/cold, lane}`.
2. **slice** — the `memory.max` (or host RAM) the peak was observed *under*.
3. **derived_cap** — the value we write to `hard_mem_max_bytes`, computed from (1)+(2), NOT copied from (1).

Why they differ: `memory.peak` is **cap-influenced**. Reclaimable page-cache from the compile
expands to fill available headroom, so a peak observed under a *generous* slice OVER-states the
true working set. Measure generous, observe the peak, then **derive** — do not adopt the peak.
(Empirical proof: `rr_suite_contract` OOM-killed at a 2.0 G slice yet PASSED at a 512 MiB slice via
reclaim. A leak cannot pass at 512 MiB; the peak==slice at OOM was the cap pulling the number up.)

## The independent variable is CORES. Two node classes, two formulas.

### A. CONFIGURABLE-POOL  →  cap = f(configured_threads)
`cargo build|test|clippy|doc|nextest`, our own test runner. Parallelism is a **knob** you set
(`CARGO_BUILD_JOBS` for compile fan-out; `--test-threads` for run). There is **no third inner
level** of parallelism, so telling the step *j* threads caps parallelism at *j* CPUs regardless of
how many cores the box has. Therefore:

    derived_cap(j) = non_reclaimable_working_set(j) * headroom
    non_reclaimable_working_set(j) ≈ base_serial + per_worker * j     (rustc/cc1plus heap swarm)

The cap is a function of the **configured** j, NOT of `nproc`. Pin j, derive at that j, record
`{j:N, bytes:M}`. NOTE even a `--test-threads=1` test node still *compiles* with fanned
cc1plus/rustc first — the **compile phase is the memory driver**, so it is class A on `CARGO_BUILD_JOBS`.

### B. FIXED-THREAD  →  cap = constant, independent of cores
`hermit --verify` guest execution (`e2e.manifest_*`, `dbi_parity`, `envelope_levels`,
`applications_e2e`). The guest runs a fixed number of threads; changing the CPU slot changes how
much *machine* it may use, not how many *threads* it spawns. `CARGO_BUILD_JOBS` does nothing here.

    derived_cap = (supervisor_rss + guest_working_set) * headroom     # a CONSTANT, not f(cores)

### HYBRID — `test.strict_compat` only
`./validate.sh --portable-strict-compat-only` = a cold release build (class A) **then** guest verify
(class B). Its 6.0 G OOM is the class-A compile phase (reverie-dbi build.rs → DynamoRIO cc1plus),
not the guest. So it is derived as class A on the *nested* build's j.

## THE PREREQUISITE — why this cannot be finalized before the CPU-quota fix
`f(configured_threads)` only holds once the threads are actually configured. Today they are NOT:
the safe-ci **CPU quota leaks into Cargo as `NUM_JOBS=284`** (task
`cpu-quota-leaks-into-cargo-num-jobs-cap-must-be-global`, owner hermit-238b, IN_PROGRESS; root fix —
a single `CI_DAG_BUILD_JOBS=8` applied where the quota is granted — in active implementation as of
2026-08-04 14:44Z, gpt-5.3-codex; 16/26 direct cargo commands currently leak). Until that lands,
class-A nodes run at leaked j≈284, so any observed compile peak is measured at the WRONG j and the
current j8-warm figures in `set-table.txt` are **lower bounds** (production leaks higher).

**The mem-cap OOM and the CPU-quota leak are the SAME KNOB viewed twice.** Deriving caps reactively
per node (#1583 → #1597 → the `strict_compat` keystone pin `1e20e4c6`) is whack-a-mole across a
growing set. The systematic fix — the global job cap at the box boundary — **subsumes** all of them:
once every boxed command inherits `j=8`, the nested `validate.sh` in `strict_compat` inherits it too,
so `1e20e4c6` becomes redundant and every *future* compile node is covered without a new pin.

## Reconciling the two facts the model must explain
- **a10f2a80 / e8a0d8d3 PASSED full portable (5/5, 378 s / newest-green 01:29Z).** Under lower box
  contention (or a warm `target/release` the nested build reused), the j≈284 cc1plus swarm's
  *simultaneous non-reclaimable* peak stayed under the 6.0 G slice, and reclaimable page-cache
  absorbed the rest.
- **b384187e / #1597 / #1592 OOM on `strict_compat`.** Cold cache + higher contention → the same
  j≈284 swarm's simultaneous heap exceeds 6.0 G → OOM at cap. Same node, same cap; the difference is
  purely the leaked j and box load. Pinning j=8 bounds the swarm deterministically → passes on any
  load. This is why the fix is the **j-pin (globally), not a bigger cap.**

## Derivation status as a set (what is unblocked vs gated)
- **Class B (18 guest nodes): UNBLOCKED and non-binding.** j-independent; all currently show SLACK
  (0.00–0.14 tightness), none OOM. Derive `constant + headroom` from a runtime measurement at any
  slice; not on the critical path.
- **Class A + HYBRID (compile-bearing): GATED on the global-cap fix.** Re-measure `observed_peak@j=8`
  under a generous slice AFTER the global cap lands, then `derived_cap = peak@8 * headroom` with a
  smaller-cap negative control that plateaus. The 20 unmeasured compile nodes are the latent OOM
  chain; do not pin them one-by-one — the global cap covers the set.

Related memories: [[dag-memcaps-four-class-split-15-unpinned-compile-nodes]],
[[dag-hard-mem-caps-are-hand-picked-round-constants]], [[1597-incomplete-oom-relocates-to-strict-compat]],
[[dag-oom-blast-radius-offender-vs-neighbour]].

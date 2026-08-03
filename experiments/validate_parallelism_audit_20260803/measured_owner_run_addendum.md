# Measured per-node addendum — owner's green run `a034f39c`

Companion to `README.md`. That audit computed the critical path and resource-cap
makespan from `hint.est_duration_s`, and correctly flagged (its Caveats) that the
hints are **~5x pessimistic** vs warm-real, so "the makespan **ratios** are valid,
the **absolutes** are not." This addendum closes that gap: every number below is a
**measured** per-node wall time mined from the owner's actual green-run log
(`/tmp/hermit-validate.c02CCi.log`, commit `a034f39c`, PASS, `real 8m18.665s` /
`user 13m09.104s`), not an estimate.

Data files: `measured_portable_nodes.tsv`, `measured_privileged_nodes.tsv`
(`dur_s \t node \t desc`, parsed from the log's per-node `Duration:` lines).

## The five top-level gates (measured, from the log)

| # | gate | measured |
|---|------|---------:|
| 1 | Initialize repository submodules | 1s |
| 2 | Centralized test manifest and inventory | 9s |
| 3 | **portable CI DAG manifest (47 nodes)** | **455s** |
| 4 | Centralized test manifest and inventory | 8s |
| 5 | privileged CI DAG manifest (7 nodes) | 26s |
| | **sum** | **499s** ≈ real 498.665s |

Confirms the README: **not 36 serial bash gates** — 5 gates, of which the portable
DAG is **91%** of wall. (The "36 run_check gates" premise is stale; the DAG
conversion already happened.)

## Q1 — Where the time actually goes (measured)

Portable DAG: 47 nodes, node-wall-sum **677s**, DAG wall **454.6s** →
effective concurrency **677/455 = 1.49x**. Privileged DAG: 7 nodes, sum 25s,
wall 25s → **1.00x (fully serial)**.

Top nodes dominate hard:

| node | measured | % node-sum | % DAG-wall | class |
|------|---------:|-----------:|-----------:|-------|
| test.strict_compat | 175s | 26% | 38% | latency-bound |
| build.dbi_release | 65s | 10% | 14% | cpu-bound |
| test.command_strict_verify | 60s | 9% | 13% | latency-bound |
| test.hermit_integration | 58s | 9% | 13% | latency-bound |
| e2e.manifest_language_runtimes | 38s | 6% | 8% | manifest |
| test.app_strict_verify | 37s | 5% | 8% | latency-bound |
| **top 6** | **433s** | **64%** | **95%** | |

By classification (measured): **latency-bound 26 nodes = 565s (83% of node-sum)**;
cpu-bound 14 nodes = 102s; light 7 nodes = 10s. **The wall is the latency-bound
tail**, and one node — `test.strict_compat` (the L2 compat matrix) — is 38% of it.

## Q2 — Serial critical path vs achievable concurrency (measured)

Two floors, both computed with measured durations via a list-scheduler that mirrors
`safe-ci-dag-runner/src/scheduler.rs` (deps + `resource_caps` + `-j`):

- **Dependency-only critical path (infinite workers, caps ignored) = 191s:**
  `e2e.metadata(7) → build.workspace(5) → doc.doctests(4) → test.strict_compat(175)`.
  The 175s strict_compat node **is** the tail; nothing can go below it without
  splitting that node.
- **`hermit_guest:1` resource floor (measured) = 430s** across the 13 latency-bound
  non-manifest nodes it serializes. This **≈ the actual 455s DAG wall** — i.e. the
  observed wall is explained almost entirely by the `hermit_guest:1` serialization,
  not by dependencies.

**Model validation (measured):** simulating the *actual* config `cap1/manifest4/j2`
reproduces **454s** vs real **455s** — the resource-cap model is faithful to reality.
This is the check the est-based README sim could not make (its absolutes were 5x off).

Measured makespan under wider caps (real warm wall-seconds, the F2 A/B baseline):

| config | wall | speedup vs actual |
|--------|-----:|------------------:|
| ACTUAL cap1 / manifest4 / j2 | 454s | 1.00x |
| hermit_cap2 / j8 | 245s | 1.86x |
| hermit_cap4 / j8 | 203s | 2.24x |
| hermit_cap4 / manifest8 / j16 | 203s | 2.24x |
| hermit_cap8 / manifest8 / j16 (= dep-floor) | 191s | 2.38x |

**Findings:**
1. **`resource_caps.hermit_guest:1` is the single binding constraint** — the whole
   455s→191s headroom is resource serialization, not dependencies. This is the
   measured confirmation of README fix **F2** (raise the cap), and it revises the
   README's est-based projections *downward*: the realistic warm ceiling is
   **~2.4x** (455s→191s), not the 5.55x–6.83x the pessimistic hints suggested —
   because warm-real builds are cheap, so the cpu-bound fanout the hints assumed
   isn't there to harvest.
2. **The floor is 191s and it's a wall.** Below 191s is impossible by any cap/-j
   change because `test.strict_compat` is a single 175s node. Beating it requires
   **sharding strict_compat** internally (README **F3**). So the two levers compose:
   F2 (cap 1→4) buys 455s→~203s; F3 (shard the 175s node) is what unlocks below ~191s.
3. The outer `-j` (README **F1**) is a warm no-op here — with `hermit_guest:1`, only
   ~1.5 hermit nodes are ever eligible at once, so more workers sit idle. F1 helps
   only the cold/mixed-cache path (builds fan out); it does **not** move the owner's
   warm 1.58x. Widen the cap, don't add workers.

## Q3 — Is 13m09s user time consistent, or is there unaccounted (blocked) time?

**Consistent — no spin pathology.** `user 789.1s` / `real 498.7s` = **1.58 avg
cores** busy; adding `sys 319s` (ptrace syscall-interception, genuine kernel
compute) gives `(user+sys)/real = 1108/499 = 2.22` avg cores. On a 316-thread box
that is **~99% of core-seconds idle** — the signature of a run dominated by
**blocking waits**, not spinning.

The distinction the owner cares about (spin vs block, per the reap-bug that pinned a
core while appearing hung): a **spin** inflates CPU toward `wall × cores` (here that
would be ~157,000 core-s); a **block** accrues wall with little CPU. We observe the
latter — 789 user-s is *modestly above* the 702s node-wall-sum, meaning there is
**no phantom CPU**: the latency-bound hermit tests run at ≈1 core with heavy
blocking (deliberate — determinism serialization + `--test-threads=1` + `hermit_guest:1`),
and warm-incremental builds add only a small parallel sliver. Every core-second is
accounted for by legitimate work. The low 1.58x is **under-dispatch + genuine
waiting by design**, exactly what a healthy latency-bound determinism suite looks
like — not the reap-bug spin.

(Contrast, same host, the failure-path validation of the validate.sh cost-honesty
change: `CPU/wall 1.7x across 316 cores` under fleet contention — also block-
dominated, never approaching a spin's `cores × wall`. The always-on wall+CPU line
added to `validate.sh` is precisely what makes this spin-vs-block call visible from
the outside on every run.)

## Feeds two consumers

- **hermit-parspeed:** measured absolutes replace the est-based ones — realistic
  warm ceiling **~2.4x** (455s→191s) from F2 alone, hard floor **191s** set by the
  175s `test.strict_compat` node (⇒ F3 required to go lower), F1 is a warm no-op.
  Model validated: actual-config sim 454s vs real 455s.
- **validate.sh estimate:** the owner's run is a legitimate **n=1 warm** basis
  (`full` profile, real 8m18.665s). The shipped `validate.sh` change already
  derives its banner estimate from the ledger and labels cache-state + n honestly;
  this run is the first warm `full` datum feeding that history.

## Reproduction

```
# measured per-node durations (from the owner green-run log)
grep -B2 '^Duration:' /tmp/hermit-validate.c02CCi.log   # per-node Duration lines
# critical path + resource-cap makespan on MEASURED durations:
#   see the inline python in the impl note; consumes measured_portable_nodes.tsv
#   + ci/dag/portable.json (deps, classification, resource_caps)
```

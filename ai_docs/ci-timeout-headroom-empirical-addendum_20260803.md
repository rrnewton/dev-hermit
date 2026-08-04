# CI timeout headroom — empirical addendum (measured budget÷reality ratios)

- **Task:** `timeout-headroom-and-load-relative` (P1). Companion to
  `ci-timeout-headroom-and-load-relative-analysis_20260801.md` (hermit-ci's
  8-surface inventory + A/B/C options + recommendation). This addendum replaces
  that analysis's *estimated* `est_duration_s` headroom ratios with **empirically
  measured** ones, and settles the "does the runner actually enforce?" question
  that gated the whole line of work.
- **Date:** 2026-08-03. Agent hermit-231b.
- **Data source:** the breach table — 5 boxed runs of the portable DAG on hermit
  `main` @ `1cea8a6f`, runner `safe-ci-dag-runner 0.11.0` (agent-utils `main` @
  `1c0e9c3`). Raw: `ignored/breach-table-231b/`; aggregate:
  `experiments/breach-table-portable-dag_20260803/breach-table.json`.

---

## 0. Does the runner ENFORCE? (the blocking question) — YES for cgroups+perf, NOT YET for cpu-time

The premise that "the Rust runner warns cgroups/perf are UNIMPLEMENTED (Python
only)" was **true on 2026-08-02** and is **stale now**. Verified empirically at
the current pin (`1c0e9c3`, 0.11.0):

| capability | at `1c0e9c3` (main) | evidence |
| --- | --- | --- |
| cgroup-v2 boxing (`memory.max`/`cpu.max`/`cgroup.kill`) | **IMPLEMENTED** | `rs/.../src/cgroup.rs` ("Rust port of `cgroup.py`"); **4 real OOM-kill events** observed in the breach runs ("hit inner MemoryMax; N oom_kill event(s)") |
| perf logging (`step_profiles_*.csv`) | **IMPLEMENTED** | `rs/.../src/perflog.rs`; **18/18 CSV rows** populated with `peak_bytes` + `cpu.usage_usec` |
| `cpu_timeout` / `RLIMIT_CPU` (load-invariant CPU-second budget) | **NOT on main** | `grep -e RLIMIT_CPU -e prlimit -e cpu_timeout rs/.../src/` = empty at `1c0e9c3` |

The `cpu_timeout` piece — the load-relative fix this task exists to produce — is
already **built and green** in **agent-utils PR #4**
(`ci/cpu-time-rlimit-timeout` @ `f1a61a1`, based on `1c0e9c3`; all 4 checks
SUCCESS incl. the Python↔Rust cross-differential parity gate), but **not yet
merged** (draft, `state=OPEN`). So the headroom numbers below are **real, not
fabricated** — they come from a runner that genuinely cgroup-boxes and perf-logs
every step; and the enforcement mechanism that acts on them exists and is
validated, pending land + config-wiring.

The stale "unimplemented" wording lives in hermit `ci/run-dag.sh` `find_runner()`
("Python … the only implementation with cgroup boxing + perf logging **in 0.1**")
— true at 0.1, false at 0.11. That comment (and the resolver default) is
`hermit-250`'s `rust-runner-lacks-cgroups-and-perf` surface; the correct capability
statement is the table above.

---

## 1. Headroom table — current WALL budget ÷ measured MAX (breach table, inverted)

`W ratio = configured wall timeout ÷ measured max wall`. `CPU ratio = wall
timeout ÷ measured max CPU-seconds`. A **huge** ratio = an absurd budget
(measuring the machine, not the test). A **small W ratio combined with `wall ≫
cpu`** = a genuine load-flake risk (wall inflated by contention, not work).

| node | wall budget | max wall | max cpu_s | W ratio | CPU ratio | n |
|------|------------:|---------:|----------:|--------:|----------:|--:|
| setup.nextest | 600 | 0.2 | 0.0 | **3922×** | 12766× | 5 |
| check.backend_abstraction | 120 | 0.1 | 0.0 | **1165×** | 5217× | 5 |
| build.flaky_harnesses | 900 | 1.0 | 0.8 | **896×** | 1184× | 5 |
| check.script_sigpipe | 60 | 0.2 | 0.1 | 387× | 429× | 5 |
| lint.rustfmt | 120 | 1.0 | 1.0 | 119× | 126× | 5 |
| doc.rustdoc | 900 | 12.1 | 34.9 | 74× | 26× | 4 |
| doc.doctests | 900 | 14.2 | 14.0 | 63× | 64× | 5 |
| build.liteinst_runtime_release | 900 | 24.8 | 237.4 | 36× | 4× | 3 |
| check.portability_paths | 60 | 2.3 | 2.2 | 27× | 27× | 5 |
| build.sabre_release | 1200 | 45.2 | 189.8 | 27× | 6× | 3 |
| lint.clippy | 1200 | 45.5 | 41.4 | 26× | 29× | 4 |
| build.workspace | 1200 | 56.3 | 918.7 | 21× | **1×** | 5 |
| build.dbi_release | 1200 | 63.1 | 892.5 | 19× | **1×** | 4 |
| build.manifest_guests | 600 | 71.9 | 13.8 | 8× | 43× | 5 |
| e2e.manifest_language_runtimes | 600 | 82.6 | 43.4 | 7× | 14× | 1 |
| e2e.metadata | 60 | 13.0 | 21.2 | **5×** | 3× | 5 |
| test.regular_crates | 900 | 272.7 | 52.5 | **3×** | 17× | 5 |
| test.detcore_unit | 900 | 299.2 | 85.2 | **3×** | 11× | 5 |
| e2e.manifest_determinism_stress | 600 | 302.9 | 16.7 | **2×** | 36× | 2 |

(~28 further nodes were empty/`--ci-only`-skipped or all-samples-invalid here —
see the breach-table caveats; derive those from CI perflog history.)

## 2. What the ratios say

**Direction 1 — absurd budgets (measuring the machine, not the test).** The
owner's "600s wall sitting unnoticed on an 8-second node" is literally
`setup.nextest` (600s budget, 0.2s reality, **3922×**), `check.backend_abstraction`
(1165×), `build.flaky_harnesses` (896×). These never flake, but they let a genuine
hang burn up to 10–15 wall-minutes before detection. A CPU-second budget derived
from measured max (`round(max(cpu_s)×1.5)`, the breach-table rule) would cap
`setup.nextest` at ~0s→a small floor, `flaky_harnesses` at ~1s — orders of
magnitude tighter hang-detection with zero flake risk.

**Direction 2 — the real load-flake risk (small W ratio AND `wall ≫ cpu`).** The
three tightest wall ratios are exactly the nodes whose wall clock is inflated by
scheduling contention rather than work:

| node | W ratio | wall/cpu | reading |
|------|--------:|---------:|---------|
| e2e.manifest_determinism_stress | **2×** | 18× | 302.9s wall on only 16.7 cpu-s — almost pure contention/scheduling. A busier box pushes wall past the 600s budget while CPU stays ~17s → **status-124 that is a load artifact, not a verdict.** |
| test.detcore_unit | 3× | 3.5× | 299.2s wall / 85.2 cpu-s, budget 900s |
| test.regular_crates | 3× | 5.2× | 272.7s wall / 52.5 cpu-s, budget 900s |

These are precisely the cells a **CPU-second budget** (PR #4) makes
load-invariant: their CPU work (17–85s) is stable, so a CPU budget of
`round(max_cpu×1.5)` (26s / 128s / 79s) fires only on genuine runaway CPU, never
on a machine that merely scheduled them slowly. A wall backstop still catches a
true no-CPU hang.

**The `RLIMIT_CPU` per-process caveat is visible too.** `build.workspace` /
`build.dbi_release` show `CPU ratio ≈ 1×` (918/892 cpu-s vs a 1200s wall budget)
because they fan CPU across 14–16 cores. A per-*process* `RLIMIT_CPU` set to the
aggregate would misfire; these multi-process-fan cells should keep the generous
wall backstop as primary (or use cgroup `cpu.stat` aggregate — deferred), exactly
as the 08-01 analysis's C-rlimit limitation predicted. Empirically confirmed.

## 3. Recommendation (unchanged direction, now empirically grounded)

1. The load-relative mechanism is **done and green** — agent-utils PR #4
   (`cpu_timeout` + `RLIMIT_CPU`, wall backstop retained, exit `152`
   distinct). Land it (coordinate for agent-utils linearity), then **wire
   per-node `cpu_timeout` into the DAG configs**, seeded from the breach-table
   derivation `round(max(cpu_s)×1.5)`, ≥5 samples else UNSET.
2. Concrete seed budgets from this run (n≥5 nodes): `e2e.metadata`=32,
   `doc.doctests`=21, `test.regular_crates`=79, `test.detcore_unit`=128,
   `build.workspace`=1378 (thin ×1.5 on a single cold sample — re-derive from CI
   history), `build.manifest_guests`=21. Single-process, contention-inflated
   cells (`determinism_stress`, `detcore_unit`, `regular_crates`) are the highest
   value — a CPU budget there converts a load-124 into a no-op.
3. Multi-core build cells (`build.workspace`, `build.dbi_release`, release
   builds) keep the wall backstop as primary; do **not** put a per-process
   `RLIMIT_CPU` at their aggregate CPU-seconds.
4. UNSET everything under-sampled here; the robust source is CI perflog history
   (no BpfJailer, warm+cold spread) via PR #1547's pipeline. Do not fabricate.

**Land order (owner directive, holds):** land the mechanism + per-node
declarations FIRST; only then tighten/flip any default. Wall budgets that are
absurdly loose (Direction 1) can be tightened in the same declaration pass since
CPU budgets replace them as the aggressive trigger while the wall stays as a
loose backstop.

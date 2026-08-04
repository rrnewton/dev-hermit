# safe-ci-dag-runner: Rust vs Python cgroups/perf capability reconciliation

**Task:** `rust-runner-lacks-cgroups-and-perf` (P0). **Author:** impl agent, opus-4.8.
**Date:** 2026-08-03. **Bound to:** dev-hermit gitlink pins agent-utils
`1c0e9c3c` (the same commit the usage audit
`ai_docs/transient/2026-08-03-safe-ci-dag-runner-usage-audit.md` bound as its
evidence at dev-hermit `dea09de3`). All source claims below are `git grep`/`git
show` against that exact `1c0e9c3` tree, not the working copy.

## TL;DR — the premise is stale

The usage audit's headline claim — *"the pinned Rust source explicitly warns
that cgroups and perf logging are UNIMPLEMENTED; the Python runner supports both
only when flags are supplied"* — is **false at its own bound pin**. At
`1c0e9c3`:

- **Rust implements cgroup boxing AND perf logging, and both are ON BY DEFAULT.**
  `rs/safe-ci-dag-runner/src/cgroup.rs` (real `memory.max` / `memory.swap.max=0`
  / `cpu.max` / `cgroup.kill` writes; oom/peak/pressure attribution) and
  `src/perflog.rs` (always-on whole-run + per-step CSV).
- **Python also implements both** (`py/safe_ci_dag_runner/cgroup.py`,
  `perflog.py`).
- The `--cgroups` flag is a **deprecated no-op** in Rust (`cli.rs`: "boxing is ON
  by default"); a missing cgroup-v2 host makes Rust **exit 3**, it does not
  silently degrade.

So the resolver preferring the Rust binary selects the *more* strictly-boxed
build, not a capability-poorer one. The audit inverted the polarity. The reason
privileged jobs historically "force Python" is **not** a Rust capability gap at
this pin; it is the resolver/binary-presence issue in §2 plus the historical
lineage where Rust boxing landed later (commit `512848a` added cgroup.rs +
perflog.rs; the audit text predates or mis-reads that).

## Deliverable 1 — Capability table (from source @ `1c0e9c3`)

Legend: **IMPL** = implemented; **DFLT-ON** = implemented and active by default;
**ABSENT** = not present; **PLAN-ONLY** = affects the sizing/planner model, not
per-step OS enforcement.

| Capability | Rust (`rs/…/src`) | Python (`py/safe_ci_dag_runner`) | Notes |
|---|---|---|---|
| cgroup memory enforcement (`memory.max`, `memory.swap.max=0`) | **DFLT-ON** `cgroup.rs` | **DFLT-ON** `cgroup.py` | BOTH box by default; `--cgroups` is a deprecated no-op in BOTH; opt out with `--allow-cgroup-failure`. Missing cgroup-v2 ⇒ exit 3. |
| cgroup cpu enforcement (`cpu.max`) | **DFLT-ON** `cgroup.rs` | **DFLT-ON** `cgroup.py` | Shared `safe-ci.slice` via `systemd-run --user --scope Delegate=yes`, per-step child cgroups. |
| small default per-step cap when a node declares NOTHING (1-core/1GB/10s floor) | **ABSENT** | **ABSENT** | No-hint step runs under the OUTER 90%-CPU shared slice only (`DEFAULT_CPU_BUDGET_FRACTION=0.90`); per-step `memory.max` set only when declared (`if let Some(m)=memory_max`). No tight per-step floor. |
| atomic subtree kill (`cgroup.kill`) | **DFLT-ON** `cgroup.rs` | **IMPL** `cgroup.py`/`teardown.py` | |
| OOM / peak / pressure attribution (`memory.events`, `memory.peak`, `cpu.stat`, `cpu.pressure`) | **IMPL** `cgroup.rs` | **IMPL** `cgroup.py` | Surfaced into perf CSV. |
| perf logging (whole-run + per-step CSV) | **DFLT-ON** `perflog.rs` | **DFLT-ON** `perflog.py` | Always on; cgroup-derived columns left BLANK when unboxed, identically on both sides. |
| `--max-mem` | **PLAN-ONLY** | **PLAN-ONLY** `cli.py` | RAM budget for the CPA planner's `-j` sizing; NOT a per-step `memory.max`. Present both sides. |
| `cpu_timeout` (per-step CPU-time budget) | **ABSENT @pin** | **ABSENT @pin** | Added only on in-flight branches — see §4. Two competing designs exist. |
| cpuset pinning (`cpuset.cpus`) | **ABSENT** (models affinity *width* only, `sizing`) | **DETECT/VERIFY only** `cgroup.py` `CONTAINER_CPUSET` | Neither build actively *pins* a cpuset; Python detects+verifies an ambient container cpuset. |
| `box` subcommand | **ABSENT** | **ABSENT** | Never existed in agent-utils history (`git log -S'"box"'` empty), yet `debug/multisect` invokes `box --mem/--cores/--timeout/--perf-dir`. See §4. |

## Deliverable 2 — The resolver, and why it is nondeterministic

`hermit/ci/run-dag.sh` selects the runner in this fixed order:

1. `$SAFE_CI_DAG_RUNNER` env override (if set).
2. `$base/rs/bin/safe-ci-dag-runner` — the compiled Rust binary, **UNTRACKED**
   (a build artifact; not committed).
3. `$base/py/bin/safe-ci-dag-runner` — the **tracked** Python entrypoint.
4. `safe-ci-dag-runner` on `PATH`.

**The defect:** an untracked, locally-built artifact (step 2) silently outranks
the tracked entrypoint (step 3). On a host where `agent-utils/setup` has built
`rs/bin`, the DAG runs the Rust build; on a clean checkout it runs Python. Same
SHA, different engine, purely from local build state — a reproducibility hole.
(At the moment of this writing `rs/bin` is NOT built on this host, so Python is
selected here; the hole is *latent*, not currently firing. The durable defect is
the design preference, not today's selection.)

**Recommendation (design only — not implemented; touches `hermit/ci`, and
`github-ci-use-dag-runner-exclusively`/hermit-ghdag owns that surface):**
make the selection tracked and explicit rather than build-state-derived. Options,
cheapest first:

- Pin the engine with a committed value of `SAFE_CI_DAG_RUNNER` (or a tracked
  `hermit/ci/dag-runner.env`) so every checkout resolves identically; CI sets it
  explicitly. This is the smallest change and removes the artifact-wins path.
- If Rust is the intended production engine, make step 2 require a tracked
  provenance marker (committed manifest naming the expected engine + a build the
  bootstrap produces deterministically), and **log the selected engine + its
  source** on every run so a silent swap is visible in logs.
- Regardless of choice: emit one line naming which of the four branches won.
  Silent selection is what makes the hole invisible.

Coordinate the actual edit with **hermit-ghdag**
(`github-ci-use-dag-runner-exclusively`, branch
`codex/portable-ci-runner-exclusive`) — they own the run-dag.sh/CI wiring.

## Deliverable 3 — What the cross-check actually covers (and skips)

`agent-utils/cross/differential.py` compares Rust vs Python:

- **Byte-identical:** `list` / `ascii` / `dot` / `json` renderings; plan/feedback/
  sizing outputs.
- **`run`:** exit code + `passed/failed/aborted/skipped` counts, under
  `--keep-going`.
- **perf CSV:** column-**SET** parity — on **UNBOXED** runs.

What it deliberately does **NOT** cover:

- Every `run` comparison is invoked with **`--allow-cgroup-failure`** (ACF) and
  the perf comparison runs **unboxed**. The dynamic cgroup `cpu.*` columns "only
  appear under boxing (**out of scope for the unboxed differential**); their
  ordering is pinned by each build's own perflog tests" (differential.py
  comments). Boxing is "proven separately" per each build's own tests.
- Therefore the green differential proves **scheduling / rendering / CSV-schema**
  parity — it does **NOT** prove live cgroup-enforcement parity between the two
  engines. A divergence in actual `memory.max`/`cpu.max` behavior would not be
  caught by the cross-check as written.
- The differential also **requires the Rust binary to be built** (`rs_command`
  errors "run `./setup rs` or `cargo build --release`" if absent) — tying test
  coverage to the same binary-presence condition as the resolver.

**Gap worth closing (design note, not in scope to write here):** add a
boxed-mode differential that asserts both engines enforce the same
`memory.max`/`cpu.max` and record the same oom/peak attribution for a known
over-budget step. Today that parity is asserted only *within* each build, never
*across* them.

## Deliverable 4 — Remaining real work, and the coordination hazard

The genuine gaps (as opposed to the stale "Rust lacks cgroups" premise):

1. **`cpu_timeout` / per-step CPU-time budget** — ABSENT at pin, and **already
   in flight on two competing branches**:
   - `codex/cpu-time-timeout` (`f43c3ea`, "Add per-step cpu_timeout") — touches
     **both** Python and Rust (io/model/scheduler/sizing/viz + tests); a
     differential-consistent design.
   - `origin/ci/cpu-time-rlimit-timeout` (`f1a61a1`, "Add per-step CPU-time
     budget (RLIMIT_CPU)") — **Rust-only**, RLIMIT_CPU approach.
   These are two *different* mechanisms (cgroup/accounting vs setrlimit) for the
   same feature. Landing both, or landing one without retiring the other, breaks
   the LINEAR-repo directive. **This needs a single owner decision before any
   further cpu_timeout code is written.**
2. **`box` subcommand** — ABSENT in both engines yet `debug/multisect` shells out
   to `box --mem --cores --timeout --perf-dir`. Either multisect is broken/
   aspirational against the current runner, or `box` is a planned CLI surface.
   Needs a decision: implement `box` as a thin single-step boxed exec, or fix
   multisect to use `run` + existing boxing.
3. **cpuset pinning** — neither build pins `cpuset.cpus`; Python only
   detects/verifies an ambient `CONTAINER_CPUSET`. If deterministic core pinning
   is wanted, it is net-new in both.

- `feat/cgroups-defaults-cpuset-231b` is currently just a **label at the pin**
  (no committed delta) — hermit-231b has not yet committed cgroup/cpuset work.
- `origin/feat/rust-cgroups-and-defaults` is a **diverged** branch (−25k lines,
  deletes estimates.rs/summary.rs/sync.rs) — NOT a clean delta on the pin; do not
  treat it as an incremental change without reconciling its base first.

**Why no code was written by this task:** deliverable #4 overlaps hermit-231b's
`enable-cgroups-and-cpu-timeouts-across-dag-nodes` and the two in-flight
cpu_timeout branches directly. Per the owner's explicit directive ("keep
agent-utils LINEAR; coordinate with hermit-231b before either of you writes")
and Hard Invariant 2 (never two agents mutating the same files), the correct move
is to hand this reconciliation to the single agent-utils writer, not to open a
third converging change.

## §5 — The stale premise extends to the sibling boxing tasks

Verifying Python's default (cli.py @ pin) shows the audit conflated the two
engines, and the confusion has propagated into three sibling P0 tasks whose cited
line numbers **do not match the pin** (they were written against an older,
opt-in tree):

- **`cgroups-opt-out-with-small-default-cap`** ("INVERT the default — boxing must
  be OPT-OUT `--unsafe-no-cgroups`"): at `1c0e9c3` boxing is **ALREADY
  opt-out/on-by-default in BOTH engines** (cli.py:4-5,217; cli.rs:21,186). The
  inversion the owner asked for is **already landed**. Two residual deltas remain:
  (a) the opt-out flag is named `--allow-cgroup-failure`, not the owner's
  requested `--unsafe-no-cgroups` (rename/UX only); (b) **the "small default cap"
  half is genuinely NOT done** — a no-hint step gets only the outer 90% slice, no
  tight 1-core/1GB/10s per-step floor.
- **`enable-cgroups-and-cpu-timeouts-across-dag-nodes`** ("NO workflow passes
  `--cgroups`; all 54 nodes lack cpu_timeout"): the `--cgroups` half is **moot**
  (deprecated no-op, boxing on by default). The **cpu_timeout half is the real
  remaining gap** (see §4 — two competing branches).
- **`per-platform-cpu-timeout-multipliers`** ("the pinned RUST runner does not
  implement cgroups"): premise stale; multipliers apply to cpu_timeout, which is
  the ABSENT piece.

Net: the boxing-default work these tasks describe is largely **already present at
the pin**. The truly-remaining, non-phantom work is a short list: (1) pick ONE
cpu_timeout mechanism and retire the other branch; (2) add the small default
per-step cap for no-hint nodes; (3) optionally rename `--allow-cgroup-failure` →
`--unsafe-no-cgroups`; (4) make CI actually invoke the runner + fix the resolver
(ghdag). This should be reflected back onto those tasks before more code lands.

## Bottom line

- The task's motivating premise (Rust lacks cgroups/perf) is **stale/incorrect**
  at the bound pin; both engines implement both, Rust boxes by default.
- The **real** durable defects are: (a) the resolver prefers an untracked
  artifact over the tracked entrypoint (reproducibility hole, latent now), and
  (b) the cross-check never validates live cgroup enforcement across engines.
- The **real** remaining features (cpu_timeout, box, cpuset) are already being
  worked — with a two-competing-designs collision on cpu_timeout that must be
  resolved by one owner to keep agent-utils linear.

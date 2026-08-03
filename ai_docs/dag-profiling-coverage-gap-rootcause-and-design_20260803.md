# DAG-profiling coverage gap — root cause + design for per-unit timing on EVERY validate run (2026-08-03)

Task: `dag-profiling-coverage-gap` (owner: hermit-coord). 250's global-validate
sweep (`ai_docs/validate-run-global-visibility-20260803.md`) found only **1 of
132** validate runs links to `safe-ci-dag-runner` per-node profiling. This doc
answers *why* ~all runs bypass the DAG runner, confirms *what/where* it persists
when it does run, and designs how to get durable per-unit timing from **every**
validate run.

Read-only investigation. All findings bind to primary `hermit` @ `36ee7e70`
(current `main`). **hermit `main` is under an incident halt** — the
implementation steps below that touch `hermit/` (validate.sh, run-dag.sh, CI
workflows) are deferred to PRs after the halt lifts; the parent-side steps and
this doc proceed now.

## TL;DR

- **The premise conflates two different timing systems.** validate.sh already
  records **per-GATE** wall time for *every* gate of *every* run; that is the
  universal signal. `safe-ci-dag-runner` records finer **per-NODE** profiling
  (cgroup/PSI/memory) but *only* for the two DAG lanes (`portable`,
  `privileged`) and *only* when its binary is present. These are not the same
  granularity and cannot be unified by "just always run the DAG runner."
- **Why ~all runs have no DAG profiling** is a stack of four causes: (RC1) most
  validate profiles never call the DAG bridge; (RC2) even the profiles that do
  hard-fail because the runner binary is absent in most worktrees
  (`agent-utils` is `update = none`); (RC3) when it *does* run, profiles land in
  per-worktree, gitignored, CWD-relative dirs (or ephemeral `$RUNNER_TEMP` in
  CI) with no host-central store; (RC4) profile↔run correlation is a timestamp
  heuristic.
- **Retention is NOT the problem.** When the DAG runner runs, it persists
  per-node CSVs append-only and unbounded (confirmed in code + on disk). The
  gap is *invocation coverage* and *scatter*, not deletion.
- **Correction to 250's doc:** the per-gate **ledger writer is already on
  `main`** (@`36ee7e70`), not just on a branch. Universal per-gate timing
  already lands going forward, minus one drop condition (unresolved parent).
- **Design:** make the already-landed per-gate ledger the universal spine (close
  its one drop + schedule the aggregator), then make the genuine DAG per-node
  profiles durable+central and exactly correlated. Full per-node metrics for
  *non-DAG* gates is a separate, larger effort — recommended deferred.

## Two timing systems (the core distinction)

| | validate.sh per-GATE | safe-ci-dag-runner per-NODE |
| --- | --- | --- |
| Unit | one `run_check` gate | one DAG node within a lane |
| Coverage | **every gate of every run** | only `portable`/`privileged` DAG lanes |
| Metrics | wall `real_seconds`, exit_code, result | wall + returncode + **cgroup cpu.\*, PSI, peak_bytes, throttling, co-tenants** (50+ cols) |
| Sink | raw log `Duration:` + JSONL ledger `gates[]` | `<perf-dir>/step_profiles_<machine>_<class>.csv` + `<machine>.csv` |
| On main? | **yes** (@36ee7e70) | yes (runner code), but rarely invoked |

The owner's expectation ("safe-ci-dag-runner should retain profiling for every
run") is only satisfiable for the *nodes it actually executes*. For the many
gates that run outside the DAG (cargo tests, smoke tests, compat sweeps, R/R,
record/replay), the DAG runner never sees them — so per-node profiling of "every
validate run" via the DAG runner alone is architecturally impossible. Per-**gate**
timing, however, is already universal.

## Q1 — Which validate paths invoke safe-ci-dag-runner?

Exactly one bridge: `run_ci_manifest_lane` (validate.sh:3215) →
`./ci/run-dag.sh <lane> -j <jobs> -v` (no `--perf-dir`). It is reached only by:

- `full` profile → `run_full_suite` runs **both** `portable` + `privileged`
  lanes (validate.sh:3325-3328).
- `portable-only` → `run_portable_only_suite` (:3225).
- `privileged-only` → `run_privileged_validation` (:3307).
- `--only <lane> <node>` fast path → one prebuilt shard via `ci/run-node.sh`
  (validate.sh:3465), a single node, not the lane.

`ci/run-dag.sh` is the shared local+GitHub entrypoint; it executes each gate of
the lane as an independently boxed `safe-ci-dag-runner` node
(`ci/dag/<lane>.json`). CI workflows (`ci-portable.yml`, `ci-privileged.yml`,
`ci-dag.yml`, `validation-levels.yml`, `ci/test_harness.sh`) call the same
script directly.

**Every other profile never touches the runner:** `quick`
(`run_quick_suite`:3313 uses direct `run_check`s + `test_harness.sh`), all
`*-compat-only` profiles, `envelope-only`, `qemu-l2-only`, and the non-DAG parts
of `super`.

## Q2 — Why do ~all runs bypass it?

Four compounding causes:

- **RC1 — profile selection.** The common local/agent runs use narrow profiles
  (`*-compat-only`, `quick`, `envelope-only`, `--only` shards) that never call
  `run_ci_manifest_lane`. Only `full`/`portable-only`/`privileged-only` do.
- **RC2 — runner absent in most worktrees (dominant cause).** `ci/run-dag.sh`'s
  `find_runner` requires the binary at `agent-utils/rs/bin/safe-ci-dag-runner`
  (or `py/bin`, or `$PATH`, or `$SAFE_CI_DAG_RUNNER`). `agent-utils` is a
  `update = none` submodule (CLAUDE.md), so most slot worktrees never
  materialize or build it. `run-dag.sh` then exits 2 ("safe-ci-dag-runner not
  found") → the DAG gate *errors* → **zero profiles**. Confirmed on disk:
  `.safe-ci-dag-runner/profiles/` exists only in worktrees that deliberately
  built agent-utils — `worktrees/{gate,ci-pinbump}/hermit` and the `scidr` dev
  tree — exactly matching 250's "scidr/gate/ci-pinbump" observation.
- **RC3 — scatter, no central store.** When it *does* run, `run-dag.sh` passes
  no `--perf-dir`, so profiles default to CWD-relative
  `<checkout>/.safe-ci-dag-runner/profiles/` — per-worktree, gitignored, and
  discarded when the slot is released. CI passes
  `--perf-dir "$RUNNER_TEMP/hermit-privileged-dag-perf"` — thrown away at job
  end. There is no host-central, durable sink.
- **RC4 — weak correlation.** validate.sh and the runner record different SHA
  fields and share no run-id, so the aggregator links profile↔run by
  slot+timestamp proximity (250 finding #2), not exactly.

## Q3 — Does it persist per-node timing when it runs, and where? (YES)

Confirmed in code (`agent-utils/rs/safe-ci-dag-runner/src/perflog.rs`) and on
disk:

- **Default dir:** `<CWD>/.safe-ci-dag-runner/profiles/`; override via
  `SAFE_CI_DAG_RUNNER_PROFILE_DIR` or `--perf-dir`; disable with `--no-profile`.
- **Files:** `<machine_id>.csv` (one row per run: timestamp, git_sha, nproc,
  wall/user/sys, result, n_steps, jobs …) and
  `step_profiles_<machine_id>_<container_class>.csv` (one row per DAG node).
  Verified header on `worktrees/gate/hermit` carries 50+ columns:
  `step, classification, inner_jobs, elapsed_s, returncode, ok, timed_out,
  oom_kills, peak_bytes, throttled_s, quota_utilization_pct, co_tenants_*,
  host/step *_psi_avg*, cpu.usage_usec, cpu.throttled_usec, …`.
- **Retention: append-only, unbounded.** `append_rows_merging_header` reads the
  existing CSV, widens the header if new columns appear, re-projects old rows,
  appends new rows; a `.lock` sidecar serializes concurrent writers; real
  deletes are `#[cfg(test)]` only. Multiple run timestamps accumulate in one CSV
  (250 verified 13 timestamps in the scidr CSV). **No retention fix needed.**

## Correction to 250's doc (material)

250 states the per-gate ledger writer "only exists on branch
`codex/validate-run-ledger` and is not yet on main." **It is now on `main`**
(@`36ee7e70`, `validate.sh` clean in the primary):

- `VALIDATION_LEDGER_FILE` defaults to
  `$DEV_HERMIT_PARENT/ignored/validate-run-ledger.jsonl`, overridable by
  `HERMIT_VALIDATE_LEDGER` (validate.sh:333-336).
- `record_ledger_gate` (:614) accumulates `{name, exit_code, real_seconds}` per
  gate; `append_validation_ledger` (:630) writes one flock-guarded JSON line per
  run from the EXIT trap (:724), including the full `gates[]` array (:662-675).

So universal **per-gate** timing already lands going forward. The one remaining
drop: when `DEV_HERMIT_PARENT` cannot be resolved *and* `HERMIT_VALIDATE_LEDGER`
is unset, `VALIDATION_LEDGER_FILE` is empty and `append_validation_ledger`
returns at :636 (no-op) — standalone/CI runs silently drop their row. The raw
per-run log (`mktemp`, always written) remains the safety net that 250's
aggregator reconstructs from.

## Design — durable per-unit timing from EVERY validate run

Layered so the universal answer (per-gate) is decoupled from the finer, narrower
per-node profiling.

### Layer A — Per-GATE timing for 100% of runs (PRIMARY deliverable)

The ledger already covers this; close its two gaps.

- **A1 — Host-stable fallback ledger path.** When `DEV_HERMIT_PARENT` is
  unresolved and `HERMIT_VALIDATE_LEDGER` is unset, fall back to
  `${XDG_STATE_HOME:-$HOME/.local/state}/hermit/validate-run-ledger.jsonl`
  instead of the :636 no-op. Eliminates the standalone/CI drop. *(hermit/ change
  — halt-deferred.)*
- **A2 — Continuously fold into the durable global store.** Schedule
  `scripts/validate-run-aggregate.py --write-global` (cron or `/loop`) to keep
  `ignored/validate-run-global.jsonl` + `.csv` current from ledger rows plus
  raw-log reconstruction of any un-ledgered runs. *(Parent-side — can start
  now.)*

Result: per-gate `{name, real_seconds, result}` for every gate of every run,
machine-wide, with historical backfill from raw logs.

### Layer B — Make genuine DAG per-node profiles durable + central

- **B1 — Persistent, central `--perf-dir`.** In `run_ci_manifest_lane` and the
  CI workflow invocations, pass an explicit host-central perf dir (e.g.
  `${DEV_HERMIT_PARENT}/ignored/dag-profiles/` locally,
  `${XDG_STATE_HOME}/hermit/dag-profiles/` when no parent) instead of the
  CWD-relative default / ephemeral `$RUNNER_TEMP`. `append_rows_merging_header`
  already tolerates concurrent multi-run accumulation into one dir. *(hermit/ +
  workflow change — halt-deferred; the *location convention* is parent-owned and
  can be fixed now.)*
- **B2 — Guarantee the runner is present where DAG lanes run.** Otherwise
  `run-dag.sh` hard-fails and there is nothing to profile. Two options:
  (i) default `SAFE_CI_DAG_RUNNER` to the primary's already-built binary
  (`${DEV_HERMIT_PARENT}/hermit/agent-utils/rs/bin/safe-ci-dag-runner`) when the
  local `agent-utils` is not materialized; or (ii) have
  `scripts/allocate-worktree.rs` materialize + build `agent-utils` for slots
  that will run DAG lanes. Option (i) is cheaper and parent-controllable.

### Layer C — Exact profile↔run correlation (fixes 250 #2)

- **C1 — Shared run-id.** validate.sh mints a run-id (e.g.
  `${host}-${VALIDATION_STARTED_EPOCH}-$$`), writes it into the ledger row, and
  threads it to the runner (env `SAFE_CI_DAG_RUNNER_RUN_ID` or a `--run-id`
  flag) so `step_profiles` rows carry the same id. The aggregator then joins
  exactly instead of by timestamp heuristic. *(Small hermit/ + runner change —
  halt-deferred.)*

### Layer D — Per-node metrics for NON-DAG gates (stretch; recommend defer)

If the owner wants cgroup/PSI/memory (not just wall time) for gates that run
outside the DAG:

- **D-full (not recommended now):** restructure validate's entire gate list to
  execute *as* a `safe-ci-dag-runner` DAG (each `run_check` = a node). Large,
  risky refactor of the authoritative 3600-line gate script (ordering,
  per-gate timeouts, conditional profiles) with real behavior-change risk to the
  merge-gate.
- **D-lite (cheaper bridge):** have `run_check` additionally append a per-gate
  row to the central perf-dir in the `step_profiles` CSV schema (wall +
  returncode only; no cgroup boxing). Gives one unified schema across DAG and
  non-DAG gates without re-architecting execution. Still a hermit/ change.

## Recommended sequencing

1. **Now (parent-side, no halt conflict):** A2 (schedule the aggregator to the
   global store); fix the central perf-dir *location convention* + the
   `SAFE_CI_DAG_RUNNER`-defaulting recipe (B1/B2 option i) as parent tooling
   ready to wire in.
2. **After the halt lifts (hermit/ PRs):** A1 (fallback path) → C1 (run-id) →
   B1/B2 wiring in validate.sh + CI workflows. These are additive,
   determinism-neutral, and confined to instrumentation.
3. **Later, only if wall-time-per-gate proves insufficient:** D-lite, then
   reconsider D-full.

A1+A2 alone convert "1 of 132 runs profiled" into "100% of runs have durable
per-gate timing." B/C make the finer per-node profiles (where the DAG genuinely
runs) durable and exactly attributable.

## Evidence

- Ledger writer on main: `hermit@36ee7e70` `validate.sh:333-336, 614-706, 724`.
- DAG bridge + profiles: `validate.sh:3215-3223, 3225-3228, 3307-3311,
  3325-3328, 3465`; `hermit/ci/run-dag.sh` (`find_runner`, no default
  `--perf-dir`); CI `--perf-dir "$RUNNER_TEMP/..."` in `ci-privileged.yml:82`,
  `validation-levels.yml:129`, `ci/test_harness.sh:346`.
- Retention: `agent-utils/rs/safe-ci-dag-runner/src/perflog.rs`
  (`append_rows_merging_header`, `append_step_profiles`, `PerfWindow`).
- On-disk profiles present only in `worktrees/{gate,ci-pinbump}/hermit` and the
  scidr tree; absent from primary and all other slots.
- Prior art: `ai_docs/validate-run-global-visibility-20260803.md`,
  `ai_docs/validate-run-ledger-reconstruction-20260802.md`,
  `scripts/validate-run-aggregate.py`.

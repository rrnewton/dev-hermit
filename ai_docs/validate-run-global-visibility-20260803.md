# Global validate-run visibility (machine-wide) — 2026-08-03

Task: `global-validate-run-visibility` (owner: hermit-250). Deliver machine-wide
visibility on *every* `hermit validate` run across all worktrees/agents, plus
confirmation that safe-ci-dag-runner retains its per-node profiling data.

This extends the (writer-only) `validate-run-ledger` to a machine-wide,
read-side aggregation. **Tool + artifacts live in the dev-hermit PARENT** per
the tracking-goes-outer principle.

## TL;DR

- **Tool:** `ci-hub/validate/aggregate.py` (parent). Sweeps all three data
  sources into one view. Read-only by default; `--write-global` persists a
  unified JSONL. Runs in ~4s.
- **Live snapshot (2026-08-03):** 131 validate runs on this host —
  `fail=63, pass=62, partial=6` — across 17 slots/worktrees
  (`liteinst=44, primary=21, 247=14, 244=12, sabre=11, …`). Only **11 of 131**
  were captured by the JSONL ledger; the other **120 were reconstructed** from
  raw `/tmp` logs and were previously invisible.
- **safe-ci-dag-runner profiling IS retained** — append-only, accumulates
  indefinitely, no cleanup/rotation. No fix needed. (Confirmed in code and on
  disk: CSVs accumulate multiple run timestamps.)

## Where validate logging lives (the three sources)

### 1. Structured JSONL ledger (write-only until now)
- **Default path:** `<dev-hermit-parent>/ignored/validate-run-ledger.jsonl`.
- **Override:** `HERMIT_VALIDATE_LEDGER=<file>` env var.
- **Writer:** `hermit/validate.sh` → `append_validation_ledger` (validate.sh
  :630-705), one row per run from the EXIT trap (:724). Per-gate durations via
  `record_ledger_gate` (:614-618).
- **Schema (one JSON object per line):** `schema_version, started_at,
  finished_at, host, slot, cwd, profile, commit, git_depth, git_ahead,
  git_behind, result, exit_code, checks, failures, real_seconds, user_seconds,
  sys_seconds, log_file, gates[]` where each gate is
  `{name, result, exit_code, real_seconds}`.
- **`slot` derivation** (`validation_slot_name`, validate.sh:37-54): `primary`
  for `<parent>/hermit`, `<slot>` for `<parent>/worktrees/<slot>/hermit`,
  `standalone` otherwise / when no parent is found.
- **Why coverage is low (11/131):** the ledger writer only exists on branch
  `codex/validate-run-ledger` and is *not yet on main*, so nearly all historical
  runs used a `validate.sh` with no ledger code. Additionally, a run whose
  `DEV_HERMIT_PARENT` cannot be resolved (some standalone runs) skips the append
  as a no-op (validate.sh:636). Ad-hoc `HERMIT_VALIDATE_LEDGER` overrides also
  scatter a few ledgers into `/tmp/hermit-validate-ledger-*.jsonl`.

### 2. Raw per-run logs (ground truth — always written)
- **Path:** `${TMPDIR:-/tmp}/hermit-validate.XXXXXX.log` (mktemp,
  validate.sh:394). Written for **every** run regardless of ledger state, and
  recorded as the `log_file` field when a ledger row exists.
- **Format:** header lines `Root:` (cwd), `Level:` (profile), then per-gate
  blocks `=== <name> ===` / `Command:` / `Exit: <n>` / `Duration: <n>s`.
- The aggregator parses these to reconstruct a ledger-equivalent record for any
  log not covered by a JSONL ledger — this is what recovers the 120 orphans.
- Snapshot: **127 raw logs** in `/tmp` (some very old runs' logs have rotated
  out; the aggregator also reconstructs from committed `ignored/**/validate-run*.log`).

### 3. safe-ci-dag-runner per-node profiling
- **Path:** `<checkout>/.safe-ci-dag-runner/profiles/` (default, relative to CWD;
  override via `SAFE_CI_DAG_RUNNER_PROFILE_DIR` or `--perf-dir`; disable with
  `--no-profile`).
- **Files:** `<machine_id>.csv` (whole-run summary: `timestamp, machine_id,
  git_sha, nproc, wall_s, user_s, sys_s, result, n_steps, …, jobs`) and
  `step_profiles_<machine_id>_<container_class>.csv` (one row per DAG node with
  `step, classification, inner_jobs, elapsed_s, returncode, peak_bytes`, PSI
  metrics, and dynamic `cpu.*` cgroup counters).
- **Writer:** `agent-utils/rs/safe-ci-dag-runner/src/perflog.rs`
  (`append_step_profiles` :427-489, `PerfWindow` :502-575,
  `append_rows_merging_header` :333-397).
- **Retention: append-only and unbounded.** `append_rows_merging_header` reads
  the existing CSV, widens the header if new columns appear, re-projects old
  rows, appends new rows. No truncation / temp-dir / rotation. A `.lock`
  sidecar is removed after writing; data is never deleted (real deletes are all
  `#[cfg(test)]`). Verified on disk: e.g. the scidr checkout CSV holds 13 run
  timestamps spanning 19:14–21:18 on 2026-08-01. **No fix required.**

## The aggregation tool

`ci-hub/validate/aggregate.py` (parent). Unifies sources 1+2 into run
records keyed by `log_file` (ledger wins; raw logs fill the gaps), indexes
source 3, and links profiling to runs by `git_sha`, falling back to
same-slot + timestamp-proximity (validate.sh and the dag-runner record different
SHA fields for the same invocation but coincident wall-clock timestamps).

```
ci-hub/validate/aggregate.py                # table, newest last + summary
ci-hub/validate/aggregate.py --profiling    # profiling coverage per checkout
ci-hub/validate/aggregate.py --write-global  # persist unified JSONL artifact
ci-hub/validate/aggregate.py --json          # unified records as JSON
ci-hub/validate/aggregate.py --csv FILE      # flat CSV
ci-hub/validate/aggregate.py --since 2026-08-03 [--slot <name>]
```

Table columns: TIME(UTC), SLOT, COMMIT, PROFILE, RESULT, GATES(pass/total),
WALL, USER, SYS, SRC (`L`=ledger / `R`=reconstructed), PROF (`y` if profiling
linked). Performance: bounded, `target/`-pruned directory walk (recursive
`glob("**")` over worktree build trees never returns); ~4s on this host.

## Durable artifacts (gitignored, in `ignored/`)
- `ignored/validate-run-global.jsonl` — unified machine-wide ledger (superset of
  the per-parent ledger; regenerate with `--write-global`).
- `ignored/validate-run-global.csv` — flat CSV of the same.

## Findings / recommendations (not implemented here — out of task scope)
1. **Make the ledger machine-wide at the source.** Once
   `codex/validate-run-ledger` lands on main, default the ledger to a stable
   per-host path even when `DEV_HERMIT_PARENT` is unresolved (e.g. fall back to
   `${XDG_STATE_HOME:-$HOME/.local/state}/hermit/validate-run-ledger.jsonl`), so
   standalone/CI runs stop dropping rows. Until then, the raw-log reconstruction
   in this tool is the safety net.
2. **Profiling ↔ run correlation is weak by git_sha** because the two streams
   record different SHA fields; the timestamp fallback links coincident runs.
   A shared run-id emitted by both validate.sh and safe-ci-dag-runner would make
   this exact.
3. Profiling is only captured in worktrees that directly invoke
   safe-ci-dag-runner (here: `scidr`, `gate`, `ci-pinbump`); the majority of
   validate runs have no profiling to link. Expected, not a defect.

# validate/run-dag engine parity — decisive answer

**Date:** 2026-08-04
**Author:** coordinator (opus-4.8), sole validate producer / Rust-port owner
**Question (owner, pending):** `ci/run-dag.sh` is a thin wrapper (`exec "$runner" run`, L121)
over TWO engines — Rust (`agent-utils/rs/safe-ci-dag-runner`, rust-script over the crate) and
Python (`agent-utils/py/safe_ci_dag_runner`). **Which engine does `$runner` resolve to in LOCAL
validate, and in GITHUB CI? Do they differ? Does a live cross-engine parity test exist?**

## Answer: BOTH canonical paths resolve to PYTHON. They do NOT differ.

The resolution chain is identical local and CI, and lands on Python in every canonical path:

### Resolver contract (`agent-utils/bin/engine-resolver`, symlinked as `common/bin/safe-ci-dag-runner`)
- L39: `ENGINE="${SAFE_CI_DAG_RUNNER_ENGINE:-python}"` — **default is Python**.
- Rust is opt-in ONLY via `SAFE_CI_DAG_RUNNER_ENGINE=rust`, and is **never a silent fallback**:
  if Rust is requested but missing/stale/tampered it REFUSES (exit 3/127), never runs Python quietly
  (L51–86). Rust also requires a build-provenance stamp matching the pinned source fingerprint.
- Logs the winning engine + exact path on every invocation (L48, L85).

### LOCAL validate
- `hermit/ci/run-dag.sh` `find_runner()` (L81–104): `$SAFE_CI_DAG_RUNNER` override → else
  `common/bin/safe-ci-dag-runner` (the resolver) → else `py/bin` → else PATH.
- `hermit/ci/run-node.sh` `find_runner()` (L81–) mirrors this "EXACTLY" (its own comment, L74).
- `hermit/validate.sh` does **not** set `SAFE_CI_DAG_RUNNER` or `..._ENGINE` (grep: no match). It
  shells out to `./ci/run-dag.sh <lane>` (L4019, L4129) and `./ci/run-node.sh` (L4382).
- ⇒ resolver runs with `_ENGINE` unset ⇒ **PYTHON**.

### GITHUB CI
- Required portable gate = **`ci-portable.yml`** (its own note: the DAG-runner `ci-dag.yml` is
  `workflow_dispatch` manual, "adds no per-PR load"). It runs nodes through `ci/run-node.sh`
  (L257) with `_ENGINE` unset ⇒ **PYTHON**.
- `ci-privileged.yml` L100 and `validation-levels.yml` L139 set
  `SAFE_CI_DAG_RUNNER=agent-utils/py/bin/safe-ci-dag-runner` explicitly ⇒ **PYTHON** (bypasses
  resolver outright).
- `ci-dag.yml` (manual) L64/L99: `_ENGINE` unset ⇒ resolver ⇒ **PYTHON**.

**Conclusion:** No local/CI engine divergence today. The Rust engine executes in **no** canonical
path unless a human sets `SAFE_CI_DAG_RUNNER_ENGINE=rust`. We test Python and ship Python. Good.
(The `error: unrecognized argument` anomaly is argparse phrasing — Python — not clap. Consistent.)

## Finding: NO live cross-engine parity test exists.
The Rust crate has only independent smoke tests — `boxing_smoke.rs`, `core_box_smoke.rs`,
`cpu_timeout_smoke.rs`, `default_cap_smoke.rs`, `unsafe_no_cgroups_smoke.rs`. Each asserts Rust
behavior and **claims** parity in a doc-comment ("at parity with the Python runner",
`cpu_timeout_smoke.rs:2`, `core_box_smoke.rs:2`, `unsafe_no_cgroups_smoke.rs:3`) — but **nothing
runs the same DAG through both engines and diffs the result.** `scratch/au-parity/` is an untracked
scratch checkout, not a landed test. So: **two implementations behind one wrapper, agreement
asserted only in prose.** Same shape as every unverified mechanism — a proxy (a comment) standing
in for the observable binding (an executed differential test).

## Consequence for the Rust port of `validate.sh`
`validate.sh` is a gate orchestrator: `run_check` → `run_check_with_timeout` → `run_timed_command`
(per-gate timeout + environmental-block retry classification + `record_ledger_gate`), plus DAG
gates that shell out to `run-dag.sh`/`run-node.sh` (⇒ Python engine). The port's stated plan binds
safe-ci-dag-runner **as a library** — i.e. the **Rust crate**. That is a real fork:

- **If `validate.rs` boxes/schedules gates via the Rust crate**, LOCAL validate (produced by the
  sole validate producer) exercises the **Rust** engine while the required GitHub gates still run
  **Python** — reintroducing the exact two-engine divergence at the validate layer. To be safe this
  MUST be paired with (a) flipping CI to Rust with build-provenance, (b) a landed cross-engine
  parity test, (c) the full 36-gate × every-profile subsumption table.
- **If `validate.rs` shells out to `$runner`** exactly as `validate.sh` does, it preserves Python
  and is a faithful port; "as a library" then applies only to the orchestration logic, not gate
  execution — no engine change.

This is a deliberate, stated migration decision, not a side effect. Owner ratification requested.

## Profiles the subsumption table must cover (from `validate.sh` L300–311) — MORE than the 4 named
`full`, `strict-compat-only`, `portable-strict-compat-only`, `rr-compat-only`, `sabre-compat-only`,
`e9patch-compat-only`, `liteinst-compat-only`, `qemu-l2-only`, `privileged-only`, `only-<lane>`,
`selective`/`--since-green`, `envelope-only`. 35 distinct `run_check` gate names.

# Portable-DAG third-party-isolation: exact edit spec + makespan prediction

**Task:** `build-workspace-forces-third-party-backends-on-31-dependents` (P0, jointly
owned hermit-dbi + hermit-perf). This is the **hermit-perf leg**: the DAG edit
shape + VERIFY-3 makespan measurement.
**Date:** 2026-08-04 19:31Z (hermit-perf, opus-4.8)
**Mechanism:** `mechanism:portable-dag-third-party-isolation` — load-bearing, reviewed.
**Do NOT land unilaterally.** Reverses part of the landed ONE-FAT-BUILD collapse
(`7f843bd6`); needs owner sign-off on the build-time/coverage tradeoff.
**Base:** hermit primary on `main` @ `f80b1c09a6e8c3ed21a2a1d5ecd57edac4862de0`
(collapse `7f843bd6` confirmed ancestor). Graph = `hermit/ci/dag/portable.json`, 45 steps.

Builds on the verified audit in
`ai_docs/dbi-dynamorio-build-fault-class-fix-scope-20260804.md`. This doc adds the
**graph-level correction** the audit needs and the **makespan prediction**.

---

## CORRECTION to the audit: the `--workspace` DynamoRIO set is FIVE nodes, not three

The audit's "--WORKSPACE coupling" list names three nodes (`build.workspace`,
`lint.clippy`, `doc.doctests`). Reading every node's **own cmd** in the committed
graph, two more carry `--workspace` and therefore compile
`detcore-dbi`/`detcore-sabre`/`hermit-install` → DynamoRIO **independently**, not
"inherited via the build.workspace root":

| node | own cmd (verbatim) | builds DynamoRIO because |
| --- | --- | --- |
| `build.workspace` | `cargo build --workspace --all-targets --features third-party-backends` | `--workspace` (and feature) |
| `lint.clippy` | `CARGO_BUILD_JOBS=8 cargo clippy --workspace --all-targets -- -D warnings` | `--workspace` (no feature — proof the lever is `--workspace`) |
| `doc.doctests` | `CARGO_BUILD_JOBS=8 cargo test --workspace --features third-party-backends --doc` | `--workspace` + feature |
| **`doc.rustdoc`** | `CARGO_BUILD_JOBS=8 cargo doc --workspace --no-deps` | **`--workspace`** (audit put this in INHERITS) |
| **`test.regular_crates`** | `CARGO_BUILD_JOBS=8 cargo nextest run ${CI:+--profile ci} --workspace --exclude detcore --exclude hermit --exclude hermetic_infra_hermit_flaky-tests` | **`--workspace`** — the 3 TPB crates are NOT in its exclude list, so they compile |

So the complete DynamoRIO-building set today is:
- **5 via `--workspace`**: `build.workspace`, `lint.clippy`, `doc.doctests`,
  `doc.rustdoc`, `test.regular_crates`.
- **1 via the release path**: `build.runtime_release`
  (`-p hermit --features third-party-backends -p detcore-dbi -p detcore-sabre -p hermit-install`, release profile).
- **10 via `-p hermit --features third-party-backends`** (debug, own cmd), all
  currently `deps:[build.workspace]` so they reuse warm artifacts rather than
  rebuild: `test.hermit_unit`, `test.cli`, `test.hermit_integration`,
  `test.arbitrary_binaries`, `test.hermit_modes`, `test.app_strict_verify`,
  `test.command_strict_verify`, `test.ignored_syscall_regressions`,
  `test.rr_suite_contract`, `doc.doctests` (doctests double-count: `--workspace`+feature).

`build.workspace` has **31 direct dependents**; **21** carry no third-party work
(the fault-isolation target set).

---

## Exact edit (ready to apply to `hermit/ci/dag/portable.json`)

**(1) `build.workspace` → product-only fat build (drop `--workspace` and feature):**
```
cargo build --all-targets
```
`--all-targets` on default-members = product crates only (default-members =
members − {detcore-dbi, detcore-sabre, hermit-install}); zero DynamoRIO. Deps
unchanged (`e2e.metadata`).

**(2) NEW node `build.third_party` (debug DynamoRIO + tpb test targets):**
```
name:  build.third_party
cmd:   cargo build --workspace --all-targets --features third-party-backends
deps:  [build.workspace]          # reuse the warm product target; compiles only the delta
```
Because it shares the one target dir and follows `build.workspace`, this is an
incremental compile of the tpb delta (the 3 crates + hermit's tpb-gated code +
DynamoRIO), built **once**, off the pure-node path.

**(3) Repoint the 10 debug TPB-feature nodes `deps: build.workspace → build.third_party`:**
`test.hermit_unit`, `test.cli`, `test.hermit_integration`,
`test.arbitrary_binaries`, `test.hermit_modes`, `test.app_strict_verify`,
`test.command_strict_verify`, `test.ignored_syscall_regressions`,
`test.rr_suite_contract`, `doc.doctests`.

**(4) The 4 remaining `--workspace` pure nodes → `--exclude` the 3 crates:**
- `lint.clippy`: `… cargo clippy --workspace --all-targets --exclude detcore-dbi --exclude detcore-sabre --exclude hermit-install -- -D warnings`
- `doc.rustdoc`: `… cargo doc --workspace --no-deps --exclude detcore-dbi --exclude detcore-sabre --exclude hermit-install`
- `test.regular_crates`: append `--exclude detcore-dbi --exclude detcore-sabre --exclude hermit-install`
- (`build.workspace` handled in (1).)

**Coverage tradeoff to surface for owner:** the `--exclude` in (4) drops
clippy/rustdoc/nextest coverage of `detcore-dbi`/`detcore-sabre`/`hermit-install`
from the pure lane. If that coverage is wanted, add a small
`lint.clippy_third_party` / test node depending on `build.third_party`. Recommend
accepting the drop initially (these crates are thin adapters) and revisiting.

---

## MUST-VERIFY before landing (VERIFY items, hermit-perf owns 1 & 3)

1. **`e2e.manifest_backend_parity_c`** deps `[build.workspace]` only (no
   runtime_release). It runs backend-parity fixtures that select `--backend dbi`
   at runtime against the **debug** binary. A product-only `build.workspace`
   yields a debug `hermit` with dbi **unavailable at runtime** → this node may
   fail or silently skip. **If it needs dbi, repoint it at `build.third_party`.**
   This is the single highest failure risk of the edit — check first.
2. `strict_compat` (deps `build.runtime_release`, `doc.doctests`,
   `test.hermit_unit`, `test.rr_suite_contract`, …) is unaffected: it already
   sits on the release/DBI branch and its TPB deps just move to `build.third_party`.
3. Re-run with DynamoRIO deliberately broken and confirm the **21 pure nodes pass**
   while only the TPB nodes red. (This is the fault-isolation acceptance test —
   the actual point of the change.)

---

## MAKESPAN PREDICTION (VERIFY-3) — with mechanism, testable

**Prediction: makespan does NOT move materially; the flat-from-j≥5 plateau does
NOT shift. The change's value is FAULT ISOLATION, not wall-clock.**

Mechanism: the makespan tail is `test.strict_compat`, whose deps include
`build.runtime_release`. `build.runtime_release` builds DynamoRIO in **release**
(≈185s cold, measured) **regardless of this edit** — it is the dependency-driven
exception the collapse deliberately kept. So the critical path
`e2e.metadata → build.workspace → build.runtime_release → strict_compat` still
carries a DynamoRIO compile. Removing DynamoRIO from the **debug** root shortens
the **pure debug** sub-paths (clippy/rustdoc/regular_crates/detcore_*/e2e), but
those are not the tail.

This is consistent with the collapse-task evidence (warm w4=315.3s vs w16=295.6s
≈ 6% for 4× width, N=1, host-confounded): the ceiling is the **serial
test/doc/release tail**, not the debug-build fan-out.

**What WOULD move makespan** (out of scope here, flag for owner): the debug TPB
nodes gain a serial hop (`build.workspace → build.third_party → TPB test`) vs
today's single `build.workspace → TPB test`. If any TPB test node is on the
critical path, this edit could *lengthen* it slightly. `build.third_party` is only
the tpb delta on a warm target, so the added hop is small, but it must be
measured, not assumed.

---

## VERIFY-3 measurement protocol (control for load — mandatory)

Full portable-DAG wall is load-dominated on this 316-core shared box (owner rule:
every wall number carries its concurrent-validate count; all-time full-5 median =
528s/n=111; recent light cluster 351/384/349/394/335s is a QUIET BOX, not a new
baseline). Protocol:
- Paired A (main `f80b1c09`) vs B (this edit), **same slot, same width, alternated**,
  N≥3 each, each rep stamped with `ci-hub load-probe` concurrent-validate count.
- Report makespan as paired deltas, not absolute medians; discard any rep whose
  concurrent-validate count or executing-CPU% differs materially from its pair.
- Primary acceptance = VERIFY item 3 (fault isolation), which is load-independent.

**Spawn-time control census:** at 19:31Z, concurrent-validate count = **1**
(one `validate.sh`; all systemd validate units terminal/`failed`), load-probe
executing CPU = **8.28%**, VERDICT SUITABLE. Good measurement window.

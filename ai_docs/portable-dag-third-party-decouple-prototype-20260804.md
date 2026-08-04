# Prototype: decouple the DynamoRIO fan-out root from the portable CI DAG

**Task:** `build-workspace-forces-third-party-backends-on-31-dependents` (P0, joint: hermit-dbi + hermit-perf)
**Author:** impl agent, opus-4.8 (hermit-dbi)
**Date:** 2026-08-04
**Status:** READY-TO-APPLY prototype for hermit-perf to land on the collapse branch. NOT applied here
(portable.json has an active concurrent editor — `codex/collapse-cargo-build-nodes-v2`, slots ghdag+opt —
and this change partially reverses that ONE-FAT-BUILD work, so it is coordinated, not raced).
**Mechanism:** `mechanism:portable-dag-third-party-isolation`. Reviewed change; do NOT land unilaterally.

File under edit: `hermit/ci/dag/portable.json`. Node names are `group.job`.

---

## Goal

A third-party (DynamoRIO/vendored-elfutils) compile fault must red ONLY the DBI/SaBRe/LiteInst-adjacent
nodes, never the ~21 pure ptrace/determinism nodes. Real evidence: 4 heads today reddened at the
`portable CI DAG manifest` gate at ~20s exit=1 (b6b3a26f, b9cadd64, 310a3689, fedc81ed) purely because
their build reached the elfutils compile; controls that did not reach it passed 5/5 (13875b09, 2a391110).

## The two coupling mechanisms (both must be broken) — verified at hermit f80b1c09

1. **`--workspace`** selects all `members`; `members − default-members = {detcore-dbi, detcore-sabre,
   hermit-install}` exactly (14 vs 11, confirmed). Each carries a NON-optional `reverie-dbi`/`reverie-sabre`
   dep, whose `build.rs` builds vendored DynamoRIO. So `--workspace` builds DynamoRIO regardless of the
   feature flag. Proof: `lint.clippy` has no feature flag yet still builds it.
2. **`-p hermit --features third-party-backends`** on individual test nodes independently enables hermit's
   `dbi`/`sabre` features → `detcore-dbi`/`detcore-sabre` → DynamoRIO, even if `build.workspace` did not.

Structural guarantee for the fix: `cargo build --all-targets` at the workspace root builds `default-members`
= product-only (hermit-cli feature-gates dbi/sabre; no default member pulls the 3 crates), so it never
compiles DynamoRIO. This is by construction, not measured.

## Node-by-node edits

### A. New node `build.third_party` (the ONLY debug node that builds DynamoRIO)
Add, identical to today's `build.workspace` command:
```
cmd: cargo build --workspace --all-targets --features third-party-backends
deps: ["e2e.metadata"]
```
(Reuse today's build.workspace hint/timeout/mem-cap block verbatim — it is calibrated for this exact compile.)

### B. `build.workspace` → product-only (no --workspace, no feature)
```
- cmd: cargo build --workspace --all-targets --features third-party-backends
+ cmd: cargo build --all-targets
```
Now builds default-members only; zero DynamoRIO. The 21 pure nodes depending on it are unblocked by a
third-party fault.

### C. Repoint the NEEDS/MIXED debug test nodes from build.workspace → build.third_party
These keep `--features third-party-backends` and need the third-party debug artifacts:
- `test.cli` (NEEDS: un-skipped `run_dbi_executes_integrated_backend`, cli.rs:455)
- `test.hermit_unit` (MIXED: `#[cfg(feature="dbi")]` unit tests in backends.rs)
- `test.hermit_modes` (MIXED: 6 sabre regression tests)
Change each node's `deps`: `"build.workspace"` → `"build.third_party"`. Leave their cmd unchanged.

### D. Drop `--features third-party-backends` from the INHERITS debug test nodes (keep dep on build.workspace)
Pure ptrace/determinism; tests select backend by runtime CLI string and runtime-skip when a backend binary
is absent, so they compile and pass without the feature:
- `test.hermit_integration`, `test.arbitrary_binaries`, `test.app_strict_verify`,
  `test.command_strict_verify`, `test.ignored_syscall_regressions`, `test.rr_suite_contract`
Edit: delete `--features third-party-backends` from each cmd. (regular_crates, detcore_*, envelope_levels,
applications_e2e already carry no feature flag — unchanged, stay on build.workspace.)

### E. `--workspace` doc/lint nodes → exclude the 3 crates (keep on build.workspace)
- `lint.clippy`:  `cargo clippy --workspace --all-targets` → add
  `--exclude detcore-dbi --exclude detcore-sabre --exclude hermit-install`
- `doc.doctests`: `cargo test --workspace --features third-party-backends --doc` →
  `cargo test --workspace --exclude detcore-dbi --exclude detcore-sabre --exclude hermit-install --doc`
  (drop the feature; the 3 crates have negligible doctests)
- `doc.rustdoc`:  `cargo doc --workspace --no-deps` → add the same 3 `--exclude`s
  (cargo doc compiles the crate it documents → build.rs → DynamoRIO without the exclude)

### F. e2e manifest nodes — VERIFY before trusting (open item)
The e2e manifest nodes run `./ci/test_harness.sh` and consume the DEBUG hermit (build.workspace) + release
artifacts (build.runtime_release). If any portable-lane manifest exercises the dbi/sabre backend through the
DEBUG binary, it needs build.third_party, not the product-only build.workspace.
- Prime suspect: `e2e.manifest_backend_parity_c` (deps build.workspace + manifest_guests). If backend-parity-c
  drives dbi/sabre via the debug binary, repoint its dep to build.third_party.
- All other e2e.manifest_* consume the debug binary only for ptrace-family runs and stay on build.workspace.
Action: run the portable manifest catalogue and grep for dbi/sabre selection through the debug binary before
landing; repoint only the nodes that need it.

## Cost / tradeoff (owner call)

Partially reverses ONE-FAT-BUILD (`collapse-separate-build-nodes-into-one-fat-cargo-invocation`, landed
2026-08-04): the third-party debug half now compiles once in `build.third_party` instead of being folded into
the single fat build. The 3 NEEDS/MIXED nodes reuse build.third_party (not N separate rebuilds). Net: one
extra debug compile of the third-party crates, off the critical path of the 21 pure nodes.

## What this does and does NOT buy on the real fixtures

- DOES: on fedc81ed / 310a3689, the 21 PURE nodes + clippy/doctests/rustdoc go GREEN even while DynamoRIO
  is unbuildable — #1595 and the pure corpus stop being blocked. This is the SPOF removal.
- Does NOT by itself: make the whole gate 5/5 on a box that cannot compile vendored elfutils —
  `build.third_party`, `test.cli/hermit_unit/hermit_modes`, and the release dbi/sabre/liteinst nodes still
  red. To get the real fixtures to full 5/5 you additionally need ONE of:
  (a) fix the DynamoRIO/elfutils build fault (environment; reverie PR #371 makes it legible), OR
  (b) move the third-party nodes to a lane ALLOWED to fail environmentally (they runtime-skip w/o artifacts;
      the only genuinely-blocking case is cli's un-skipped run_dbi_executes_integrated_backend), OR
  (c) accept dropping dbi/sabre/cli-dbi coverage on the portable lane.
  This is the NEEDS-node lane decision for hermit-perf + owner.

## Validation plan (for whoever applies it)

1. Structural (done): `members − default-members = {detcore-dbi, detcore-sabre, hermit-install}`; product-only
   `cargo build --all-targets` cannot reach DynamoRIO.
2. `cargo build --all-targets` in a slot, `-v 2>&1 | grep -ci dynamorio` == 0.
3. Run the modified portable DAG at fedc81ed and 310a3689: expect the 21 pure nodes GREEN; the third-party
   nodes red iff DynamoRIO is still unbuildable (that is correct isolation, not a regression).
4. hermit-perf VERIFY-3: re-measure makespan — does flat-from-j≥5 move once the fan-out root drops DynamoRIO?

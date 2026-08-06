# Third-party backends downstream of the first-party build — DAG edge + gating design

**Task:** `third-party-backends-move-downstream-of-the-first-party-build-in-the-dag` (P0)
**Date:** 2026-08-05
**Bound to:** hermit main **`b64d893ae9ea6404472eae9cb86102d91ec642ef`** (`ci/dag/portable.json`, 47 nodes)
**Mode:** local design + graph analysis. No validate-run, no egress, nothing mutated.

---

## Status first: the fix is designed, implemented, accepted — and **not landed**

PR **#1607** (`feat/portable-dag-third-party-isolation`, head `c9e4b96b`) implements the
`build.workspace` → `build.third_party` split, and owner acceptance was completed on 2026-08-04
(N = 46/46 clean corpus; per-node build-level mutation isolation; static independence).

**At current main it is absent.** `build.third_party` does not exist, and the fan-out root still
reads:

```
build.workspace: cargo build --workspace --all-targets --features third-party-backends
```

So the structural defect the owner named is live today. PR state is not verifiable this session
(egress down); the code state is measured directly.

## Current coupling, measured at `b64d893a`

| | nodes | share |
|---|---:|---:|
| portable DAG | 47 | — |
| **coupled to a third-party-bearing build root** | **38** | **81 %** |
| independent today | 9 | 19 % |

The 9 independent nodes are all trivial: `check.{reverie_pin, skill_discovery, backend_abstraction,
portability_paths, script_sigpipe}`, `setup.nextest`, `e2e.metadata`, `lint.rustfmt`,
`build.manifest_guests`.

**Two fan-out roots, not one** — and they are *siblings*, both `deps: ["e2e.metadata"]`:

| root | transitive dependents | third-party in its own cmd |
|---|---:|---|
| `build.workspace` | **32** (68 %) | `--features third-party-backends` |
| `build.runtime_release` | **9** (19 %) | `--features third-party-backends -p detcore-dbi -p detcore-sabre` |

**PR #1607 fixes only the first.** The release root is untouched, which is the residual the
2026-08-04 acceptance note flagged but did not design. That is the gap this document closes.

### The number that makes the case

Of the 38 coupled nodes, only **14 name third-party in their own command**. The other **24 inherit
the coupling purely through a dependency edge** — they contain no third-party content at all.

**51 % of the portable DAG pays DynamoRIO's build cost and carries its failure risk for no reason.**
That is why one third-party compile error turned four heads red at ~20 s on `portable CI DAG
manifest`.

## Why nodes are coupled — three distinct mechanisms, three distinct fixes

Classifying by *cause* rather than by node name, because each cause needs a different edit:

| # | Cause | Example | Fix |
|---|---|---|---|
| **C1** | Root builds with the feature, dependents inherit the artifact | `build.workspace` `--features third-party-backends` → 32 dependents | **Split the root**: make it product-only; add a downstream `build.third_party`. *(#1607 does this)* |
| **C2** | `--workspace` scope sweeps third-party crates into a first-party command | `lint.clippy` `cargo clippy --workspace --all-targets`; `doc.rustdoc` `cargo doc --workspace`; `test.regular_crates` `cargo nextest --workspace` | **`--exclude detcore-dbi --exclude detcore-sabre --exclude hermit-install`**. *(#1607 does this)* |
| **C3** | Pure edge artifact — the node needs the *product* binary, not third-party | `test.detcore_unit` (`cargo test -p detcore --lib --bins`, package-scoped); all `e2e.manifest_*` except `backend_parity_c` (prebuilt guests) | **Repoint the edge** to the product build. No command change. |

A fourth, non-defect category: nodes that are *semantically* third-party even though their command
does not name a feature flag — `test.dbi_parity` (`--backend dbi --require-backend` on
`target/release/hermit`) and `e2e.manifest_backend_parity_c`. These **should** stay downstream; they
are correctly coupled. Any design that "fixes" them has broken coverage rather than fixed the graph.

## Proposed edge design — split both roots symmetrically

```
                     e2e.metadata
                    /            \
   build.workspace                build.runtime_release        ← BOTH product-only
   (cargo build --all-targets)    (cargo build --release -p hermit)
        |         \                     |            \
        |          \                    |             \
   ~24 first-party  build.third_party   ~5 first-party  build.third_party_release
   nodes (C2/C3)    (--features tpb,    e2e nodes       (--features tpb, release,
                     -p detcore-dbi                      -p detcore-dbi -p detcore-sabre
                     -p detcore-sabre)                   -p hermit-install)
                          |                                      |
                    debug tpb nodes                    test.dbi_parity, sabre/liteinst
                    (hermit_unit, cli, …)              strict, backend_parity_c,
                                                       build.liteinst_runtime_release
```

**Rules the graph must satisfy:**

1. **No first-party node may have a path to a third-party build node.** This is the acceptance
   property, and it is statically checkable — no run required.
2. **Third-party build nodes are leaves-of-the-product-build**, never ancestors of it.
3. **Both roots split, or the property does not hold.** With only `build.workspace` split, a
   DynamoRIO break still fails `build.runtime_release` and its 9 dependents — including
   `test.strict_compat`, which is the join point (`deps` includes `build.runtime_release` plus six
   others).

**Expected payoff:** first-party insulated nodes go from **9/47 (19 %) → ~31/47 (66 %)**. The
remaining ~16 are genuinely third-party and correctly fail with it.

## Node gating

Gating is a *second* mechanism from edge placement, and it should stay small:

- **Feature-availability gate.** Third-party nodes already carry `--features third-party-backends`;
  the gate is the cargo feature itself. Do **not** add a parallel enable/disable flag — a second
  switch is the drift bug (the `-j` default at two lines, the ledger path under three names).
- **Skip must be NAMED, never silent.** If the third-party subtree is skipped (feature off, toolchain
  absent, upstream failure), the run record must say which nodes were skipped and why. A silently
  shrunken node set that reports green is the `filtered == 0 is not completeness` failure.
- **`--require-backend` stays.** `test.dbi_parity` already passes it; that is what stops a
  "backend unavailable → silently pass" degradation. Keep it on every third-party runtime node.
- **The subtree gets its own verdict.** A third-party failure should render as
  `third-party: FAILED, first-party: PASSED`, not as a whole-DAG red. Otherwise the availability win
  is invisible in the receipt even though it is real in the graph.

## Acceptance — and the trap that invalidates the obvious test

The owner's property: *break DynamoRIO → first-party nodes still pass, only third-party fail; then
confirm the first-party corpus still passes at full strength, N stated.*

**You cannot demonstrate this with a full-DAG mutation run.** The safe-ci scheduler is
**fail-fast**: `--keep-going`/`-k` lets already-running steps finish but **still stops launching new
steps** (`agent-utils/py/safe_ci_dag_runner/scheduler.py:33,564-568`; `cli.py:216`). Un-launched
first-party nodes never run, so the result is ambiguous — observed previously as RUN2 stopping after
6 nodes in 8.3 s.

Use instead, in this order:

1. **Static check (cheap, no build).** Assert no first-party node has a dependency path to a
   third-party build node. This is the acceptance property expressed directly, and it belongs in CI
   as a lint so the edge cannot regress.
2. **Per-node build-level mutation.** Inject `compile_error!` at **EOF of
   `detcore-dbi/src/lib.rs`** and run each node's actual command independently. Two traps already
   paid: `reverie-dbi` is a **pinned git dep**, so mutating the reverie worktree is *vacuous* (cargo
   builds from `~/.cargo/git`); and the `compile_error!` must be at a valid item position — placing
   it before the `//!` doc comment yields E0753, which breaks `cargo fmt` and corrupts the test by
   failing a *first-party* node.
3. **Full-strength clean run, N stated.** Prior evidence: N = 46/46 in 308.5 s at slot head
   `c740ee9a`. Re-establish at the post-split head so the pass is not bought by dropped coverage.

## Recommendation

1. **Land #1607** — designed, implemented, accepted; it removes the 68 % root. Blocked only on
   egress and review.
2. **Add the symmetric release split** (`build.runtime_release` → product-only +
   `build.third_party_release`). Small, mirrors #1607, and without it the acceptance property is
   false for 9 nodes.
3. **Add the static edge lint** from acceptance step 1 so the property is enforced rather than
   re-measured.
4. **Repoint C3 edges** — pure edge artifacts like `test.detcore_unit` need no command change at all.

Sequenced this way, each step is independently landable and step 3 prevents the regression that
would otherwise arrive with the next DAG edit.

## Provenance

| Claim | Source | Status |
|---|---|---|
| `build.third_party` absent; `build.workspace` still `--features third-party-backends` | `hermit/ci/dag/portable.json` @ `b64d893a` | **measured this session** |
| 38/47 coupled · 9 independent · 32 and 9 dependents · 14 own-cmd vs 24 incidental | transitive-closure over the same file | **computed this session** |
| C1/C2/C3 causes, per-node commands and `deps` | same | **read this session** |
| Scheduler fail-fast (`scheduler.py:33,564-568`) | agent-utils `570e7865` | **read this session** |
| PR #1607 head, N = 46/46, mutation-isolation result, pinned-git-dep and `compile_error!` traps | task notes, 2026-08-04 | inherited; **PR state not verifiable — egress down** |

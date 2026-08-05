# Can cargo derive a file/crate → component map (Buck2-style), instead of a hand-maintained one?

**Owner-requested research, 2026-08-04.** Feeds the deferred cache design and affected-test
selection. A hand-maintained crate→component map has drifted four times today; the question is
whether cargo itself can *derive* the dependency edges so both test selection and any future
caching key off a real graph, not a guessed one.

**No implementation here** — this is an evidence report. All commands below were run against the
live workspaces on the pinned nightly (`cargo 1.99.0-nightly (3efb1f477 2026-07-17)`); both
`reverie/` and `hermit/` are on nightly toolchains, so `-Z unstable-options` is available.
Raw outputs: `scratch/cargo-graph-research/` (`metadata.json`, `unitgraph.json`,
`hermit-metadata.json`, `tree.err`).

## Verdict up front

- **YES, cargo derives a real, resolved, feature-aware dependency graph at CRATE/TARGET
  granularity.** Both `cargo metadata` (stable) and `cargo build --unit-graph` (nightly) emit it
  as JSON with dependency edges. We can compute "change in crate X → which test targets" purely
  from this, with **zero hand-maintained edges** — *within a single workspace*.
- **NO, cargo does NOT reach Buck's file-level granularity.** Buck2 tracks per-action *file
  inputs*; cargo's graph node is a *compilation unit* whose only recorded source is the crate
  **root** (`lib.rs`/`main.rs`). Cargo *does* rebuild on any file change under a crate, but it
  does so via internal fingerprint mtime-scanning that is **not exposed** in any graph output.
  So you cannot ask cargo "which crate does `reverie-kvm/src/executor.rs` belong to and what does
  that affect" — you get crate-level, not file-level, edges.
- **Cross-repo (hermit→reverie) is coarser still:** hermit consumes reverie as a **git-pinned
  package**, not a path dep, so a change in the `reverie/` primary checkout is *invisible* to
  hermit's cargo graph until the pinned rev is bumped (see §4).
- **Is crate-level enough for us?** For **test selection** — yes, and it is strictly better than
  the drifting hand-map. For **caching** — crate-level is a correct but *coarse* cache key
  (any file touch invalidates the whole crate's unit); acceptable, but no finer than cargo's own
  incremental fingerprint already gives. See §5.

## 1. `cargo metadata --format-version 1` — resolved, feature-aware, package-level

`cargo metadata --format-version 1 --offline` (reverie workspace): **rc=0, 1.23 MB, no network.**
Top keys: `packages, workspace_members, workspace_default_members, resolve, target_directory,
build_directory, version, workspace_root, metadata`. **287 packages, 25 workspace members,
`resolve` present.**

What it gives:
- **`resolve.nodes[]`** — the *resolved* dependency graph (post feature-unification, post
  version-selection). Each node: `{id, dependencies, deps, features}`.
  - `features` = the **resolved feature set** for that crate in this build → **feature-aware.**
  - `deps[].dep_kinds[]` = `{kind, target}` where `kind` ∈ {null(normal), "dev", "build"} and
    `target` = the `cfg(...)`/triple gate → **dependency-kind- and platform-aware.**
- **`packages[].targets[]`** — every build target per crate with its kind. This is the crate →
  test-target map we need. Example, `reverie-kvm`:
  `lib reverie_kvm` + test targets `counter, static_elf, strace, vmcall`.

**Answering "which crates depend on `reverie-kvm`":** invert `resolve` edges. Direct dependents:
`reverie-examples`, `reverie-sabre-strace`. Reverse-*transitive* closure over workspace members
= **3 of 25 crates** (`reverie-kvm` itself, `reverie-examples`, `reverie-sabre-strace`), and from
their `targets[]` we get the exact affected test set, e.g. `reverie-examples` tests
`e9patch_direct, kvm_cli, liteinst`; `reverie-kvm` tests `counter, static_elf, strace, vmcall`.
**This closure is computed entirely from metadata — no hand-maintained map.**

Does it distinguish `reverie-kvm` vs `reverie` core? **Yes** — they are distinct resolve nodes
with distinct ids and distinct reverse-dependent sets. A dependent of `reverie` core is not
implied to depend on `reverie-kvm`.

## 2. `cargo build --unit-graph -Z unstable-options` — the Buck action-graph analogue

`cargo build --unit-graph -Z unstable-options --offline -p reverie-kvm`: **rc=0, 123 KB.**
**Nightly-only** (`-Z unstable-options`); fine for us — both repos pin nightly. It computes the
graph and **exits without building.**

Top keys: `version(=1), units, roots`. **148 units.** Each unit:
`{pkg_id, target, profile, platform, mode, features, dependencies}`.

This is genuinely closer to Buck's action graph than metadata: it splits a crate into **separate
units per (target, profile, mode, feature-set, platform)** — e.g. the `lib` build unit is a
different node from the `test` unit and from a build-script `run-custom-build` unit, and a dep
compiled under two feature sets appears twice. `dependencies[]` are edges between these units, and
`roots[]` names the requested units.

**But the granularity floor is still the compilation unit, not the file.** A unit's `target` has
`src_path` = **a single file, the crate root** (`.../reverie-kvm/src/lib.rs`) — *not* the set of
`.rs` files that compose the crate. There is no per-file input list anywhere in the unit graph.
So unit-graph refines *how a crate is built* (profile/feature/mode splits) but does **not** tell
you that `executor.rs` vs `memory.rs` changed — both map to the same `reverie_kvm` lib unit.

## 3. `cargo tree --invert` — human-oriented, but cleanly answers "what depends on X"

`cargo tree --workspace -i reverie-kvm --offline` → clean reverse tree:
```
reverie-kvm v0.2.0 (…/reverie/reverie-kvm)
├── reverie-examples v0.2.0 (…/reverie/reverie-examples)
└── reverie-sabre-strace v0.2.0 (…/reverie/experimental/reverie-sabre-strace)
```
matches the metadata inversion. Note **`--workspace` is required** — bare `cargo tree -i X`
scoped to default members returned only `reverie-kvm` with no dependents (an easy false-negative
trap). `-e features` adds feature-labeled edges. Useful for humans/spot-checks, but for a derived
map prefer `cargo metadata` JSON (stable, parseable, no tree-formatting to scrape).

## 4. Cross-repo caveat: hermit consumes reverie as a git-pinned package

`cargo metadata` in **hermit/** shows all **15** `reverie*` crates (including `reverie-kvm`) with
`source = git+https://github.com/rrnewton/reverie.git?rev=79517704…`, **not** a path source.
Consequences for any hermit-side derived map:
- A change to `reverie/reverie-kvm/src/foo.rs` in the **primary checkout is invisible** to
  hermit's cargo graph until the pinned `rev` is advanced. The graph maps to the `~/.cargo`
  git checkout of the pinned rev, not the working tree.
- When the rev *is* bumped, cargo can only tell you *which hermit crates depend on some reverie
  crate* — at package granularity. It cannot localize the change to a reverie file or even a
  single reverie sub-crate boundary from hermit's side (the whole pinned rev moves atomically).
- **Therefore a cross-repo file→target map is NOT derivable from cargo.** Within-repo it is;
  across the git-pin boundary it is not. (This is exactly the kind of boundary a Buck2 monorepo
  erases and cargo's multi-repo pin does not.)

## 5. How much coarser than Buck, and does the gap matter?

| Dimension | Buck2 action graph | cargo (`metadata`/`unit-graph`) |
|---|---|---|
| Node granularity | per-action, **file-level inputs** | **compilation unit** (crate×target×profile×mode×features) |
| "which target does file F affect?" | yes, F is a declared input | **no** — only crate-root `src_path`; per-file rebuild is internal fingerprint, unexposed |
| feature/cfg aware | yes | **yes** (`features`, `dep_kinds.target`) |
| dev/build/normal split | yes | **yes** (`dep_kinds.kind`; unit `mode`) |
| cross-repo file mapping | yes (monorepo) | **no** (git-pinned opaque package, §4) |
| derived, no hand-map | yes | **yes, within a workspace** |

**The one real gap is file→crate attribution.** But note it is *cheaply closable outside cargo*:
map a changed path to its owning crate by walking up to the nearest `Cargo.toml` (each workspace
member is a directory subtree). That is a deterministic filesystem lookup, not a hand-maintained
edge list, so it does **not** reintroduce drift. Combined with cargo's derived crate→crate edges,
this yields a fully-derived `file → crate → reverse-transitive crates → their test targets`
pipeline with **no hand-maintained mapping** — the property we wanted.

## Bottom line for the cache / test-selection design

1. **Replace the hand-maintained crate→component map with `cargo metadata --resolve`.** It is
   resolved, feature/kind/platform-aware, stable (not nightly-gated), offline-capable, and
   distinguishes `reverie-kvm` from `reverie` core. This removes the drift class outright, *within
   a workspace*.
2. **File→crate is not in cargo, but is a drift-free `Cargo.toml`-boundary lookup**, not a guess.
   Cargo will not give per-file edges (unit `src_path` is crate-root only), and that is the sole
   place we fall short of Buck.
3. **`--unit-graph` buys profile/feature/mode split** if the cache key needs to distinguish, e.g.,
   a `dev` vs `release` or feature-varied build of the same crate; otherwise `metadata` suffices.
4. **Do not attempt a cross-repo (hermit↔reverie) file map from cargo.** The git pin makes reverie
   opaque to hermit's graph; treat a reverie-rev bump as invalidating all hermit crates that
   depend on any reverie crate (package granularity), and do reverie's own selection inside the
   reverie workspace.

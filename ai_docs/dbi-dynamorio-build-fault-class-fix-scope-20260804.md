# Class fix: a third-party DynamoRIO build fault must not red out non-DBI PRs

**Task:** `reverie-dbi-build-rs-dynamorio-panic-blocks-unrelated-prs` (P0)
**Date:** 2026-08-04
**Author:** impl agent, opus-4.8 (hermit-dbibuild)
**Status:** DESIGN for coordinator/owner review — load-bearing CI mechanism (`mechanism:portable-dag-third-party-isolation`). Do NOT land unilaterally.

This is the part-3 deliverable ("DECIDE WHETHER a DBI build fault should red out
non-DBI PRs at all"). Parts 1–2 (diagnosis + named build.rs error) are shipped
as **reverie PR #371** on branch `fix/reverie-dbi-buildrs-named-cmake-error`.

---

## The mechanism, stated exactly

The portable CI DAG (`hermit/ci/dag/portable.json`) has a single universal
fan-out build root, **`build.workspace`**:

```
cargo build --workspace --all-targets --features third-party-backends
```

**21 of 31 downstream nodes** depend (transitively) on `build.workspace` and
carry NO third-party backend work of their own — the whole e2e manifest set (13
nodes), `detcore_unit`, `detcore_misc`, `detcore_parallel`, `regular_crates`,
`envelope_levels`, `applications_e2e`, `rustdoc`, `clippy`, `strict_compat`. If
`build.workspace` fails, every one of them is blocked and the gate reds out —
for a PR that never touched DBI.

### Why `build.workspace` builds DynamoRIO — and the sharp correction

`hermit/Cargo.toml` deliberately puts the three third-party-dependency crates
(`detcore-dbi`, `detcore-sabre`, `hermit-install`) in `members` but NOT in
`default-members`, so a bare `cargo build` produces the ptrace/kvm/liteinst
`hermit` with no DynamoRIO/SaBRe dependency. The feature `third-party-backends`
is the flag that opts them back in for the *binary link*.

**But the flag is not what forces the DynamoRIO build.** `detcore-dbi` is a
workspace `members` entry with a **non-optional** dependency on `reverie-dbi`
(`detcore-dbi/Cargo.toml:19`, no `optional`, `default-features = false`). Cargo's
`--workspace` selects *all* members and overrides `default-members`, so
`--workspace` compiles `detcore-dbi` → `reverie-dbi` → runs
`reverie-dbi/build.rs` → **builds vendored DynamoRIO**, whether or not
`--features third-party-backends` is present.

Proof by a second node: **`lint.clippy`** runs
`cargo clippy --workspace --all-targets -- -D warnings` with **no feature
flag at all** and still compiles the third-party crates (hence still builds
DynamoRIO) purely because of `--workspace`.

**Consequence for any fix:** dropping `--features third-party-backends` alone does
NOT decouple. The lever is `--workspace`. To stop the universal root from building
DynamoRIO you must select product crates explicitly — either
`--workspace --exclude detcore-dbi --exclude detcore-sabre --exclude hermit-install`
or an explicit `-p …` product set.

### Why hosted CI is green while local boxed validate reds

`ci-portable.yml`/`ci-privileged.yml` build this exact DynamoRIO correctly at the
exact head. Only the LOCAL boxed `validate.sh` fails, because the failure is
environmental in this box — host gcc/binutils vs the pinned DynamoRIO's vendored
elfutils on CentOS Stream 9, and cc1plus OOM under tight per-step cgroup memory
caps compiling drcachesim. WHY-NOW is the environment moving under a pinned
vendored dep, not the dep changing. (Full multi-modal failure catalogue —
OOM / undefined-reference link / drsyms `Aborted` — is in the task's 18:52 note.)

## The two separable levers

### Lever A — topology decoupling (durable, higher value, needs review)

Split the fan-out root so the third-party build is a *leaf-ward* node, not the
universal root:

- `build.workspace` → product-only crate selection (drop `--workspace`'s implicit
  third-party members via `--exclude` × 3; drop `--features third-party-backends`).
  All 21 pure nodes now build against a root that never touches DynamoRIO.
- New `build.third_party_backends` (or fold into the existing
  `build.runtime_release`, which already carries `DBI`) owns the
  `--features third-party-backends` / third-party-crate compilation. Only the
  10 TPB nodes (`cli`, `hermit_unit`, `hermit_integration`, `arbitrary_binaries`,
  `hermit_modes`, `app_strict_verify`, `command_strict_verify`,
  `ignored_syscall_regressions`, `rr_suite_contract`, `doctests`) depend on it.
- `lint.clippy` likewise excludes the three third-party crates (or gets a separate
  `clippy.third_party` node).

A DynamoRIO build fault then reds only the 10 DBI/SaBRe/LiteInst-adjacent nodes,
never the 21 pure ones.

**Cost / tradeoff — this partially reverses today's ONE-FAT-BUILD collapse**
(task `collapse-separate-build-nodes-into-one-fat-cargo-invocation`, landed
2026-08-04): the point of one fat `--workspace` build was that every downstream
node reuses one compiled target set. Splitting the root means the 10 TPB nodes
rebuild the third-party half separately from the pure root. Whether the
isolation is worth the extra build time is the OWNER's call. This is why it is a
reviewed mechanism change, not a unilateral edit.

**Verification items before landing Lever A:**
1. Confirm the debug `hermit` from the product-only `build.workspace` still
   satisfies every e2e manifest node. `manifest_backend_parity_c` (portable lane,
   `--prebuilt`, depends on `build.workspace`) must not require a dbi/sabre-enabled
   debug binary; if it does, repoint it at the third-party build node.
2. Confirm `strict_compat` (depends on `build.runtime_release`, the release path)
   is unaffected — it already sits on the release/DBI branch.
3. Re-run `b9cadd64` validate and confirm the pure nodes pass even with DynamoRIO
   deliberately broken.

### Lever B — classification (lower topology risk, respects ONE-FAT-BUILD)

Leave the topology; teach the fault classifier that a build failure whose output
carries the third-party marker is INFRA/third-party, not a product-PR red.
**reverie PR #371 emits exactly that observable marker**:
`cargo:warning=reverie-dbi: DynamoRIO build failed … this is a third-party
dependency fault, not a reverie-dbi/Hermit product defect`. A classifier keying
on that (bound to the source line, not a guessed substring) satisfies goal (b)
"not a product red" without reversing ONE-FAT-BUILD.

**Caveat:** the portable-DAG fault classifier is currently GREEN-but-INERT and
the interpretable-evidence producer DROPS `failed_substep_classes` (see memory
`portable-dag-gate-collapse-classifier-inert`,
`emit-interpretable-evidence-producer-drops-failed-substep-classes`). Lever B
therefore depends on first repairing that machinery. It does not, by itself,
stop the pure nodes from being *blocked* — it only changes how the failure is
*attributed*.

## Recommendation

- **Lever A is the durable class fix** — it stops 21 unrelated nodes from being
  blocked at all. Recommend it, gated on owner sign-off for the ONE-FAT-BUILD
  tradeoff and the three verification items above.
- **Lever B is complementary**, not a substitute: even with A, the 10 TPB nodes'
  DynamoRIO failures should be attributed to INFRA rather than counted as product
  reds. The #371 marker is the signal; wiring it waits on the classifier repair.
- Ship #371 regardless (parts 1–2): it makes the fault legible and provides
  Lever B's signal.

## AUDIT: "what breaks without the feature?" (needs vs inherits)

Requested by the joint root task `build-workspace-forces-third-party-backends-on-31-dependents`
(jointly owned with hermit-perf). Verdicts from a full read of the test files.

**Structural fact that decides most of it:** NO integration-test file in
`hermit-cli/tests/` contains any `#[cfg(feature = …)]`. Tests select a backend by
runtime CLI string (`--backend dbi|sabre|liteinst|kvm`), so **every test target
COMPILES without the feature**; the feature only decides whether the backend
*works at runtime*. All compile-time gating lives in the source crate
(`hermit-cli/src/lib.rs` `Backend::unavailable_reason()` with `#[cfg(not(feature
= "dbi"/"sabre"/"e9patch"))]` stubs; `run_dbi` at `lib.rs:1331` is
`#[cfg(feature = "dbi")]`). liteinst is NOT feature-gated (`reverie-liteinst` is
a non-optional dep); it needs a built DSO, not the feature.

| DAG node | own cmd uses | verdict | action |
| --- | --- | --- | --- |
| `test.cli` | `-p hermit --features tpb` | **NEEDS** | keep feature → third-party side. Un-skipped `run_dbi_*` tests assert dbi success (e.g. `run_dbi_executes_integrated_backend` cli.rs:455) and fail without it. |
| `test.hermit_unit` | `-p hermit --features tpb` | **MIXED** | keep feature. dbi unit tests in `backends.rs` are `#[cfg(feature="dbi")]` and vanish without it; rest pass. Keeping it preserves that coverage. |
| `test.hermit_modes` | `-p hermit --features tpb` | **MIXED→effectively INHERITS** | 6 sabre regression tests `run_bounded_sabre_strict_verify`; they runtime-skip when sabre artifacts absent, so without feature they skip cleanly. Safe to decouple, but decoupling drops the sabre coverage. |
| `test.hermit_integration` (determinism family) | `-p hermit --features tpb` | **INHERITS** | drop feature (safe). Pure ptrace/determinism. |
| `test.arbitrary_binaries` | `-p hermit --features tpb` | **INHERITS** | drop feature (safe). |
| `test.app_strict_verify` | `-p hermit --features tpb` | **INHERITS** | drop feature (safe). |
| `test.command_strict_verify` | `-p hermit --features tpb` | **INHERITS** | drop feature (safe). |
| `test.ignored_syscall_regressions` (epoll/rcx) | `-p hermit --features tpb` | **INHERITS** | drop feature (safe). |
| `test.rr_suite_contract` | `-p hermit --features tpb` | **INHERITS** | drop feature (safe). |
| `doc.doctests` | `--workspace --features tpb --doc` | **--workspace coupling** | drop `--workspace` (or `--exclude` the 3 crates); the feature is irrelevant here. |
| `build.workspace` | `--workspace --all-targets --features tpb` | **--workspace coupling** | use `cargo build --all-targets` (default-members product set); drop feature. |
| `lint.clippy` | `--workspace --all-targets` (no feature!) | **--workspace coupling** | `--exclude detcore-dbi --exclude detcore-sabre --exclude hermit-install` (or drop `--workspace`). |
| DBI/SaBRe/liteinst nodes (`dbi_parity`, `sabre_examples`, `liteinst_strict`) | on `build.runtime_release` | **NEEDS** | unchanged — already the third-party side. |

**Two independent coupling mechanisms — both must be broken:**
1. **The feature flag** on per-node `-p hermit --features third-party-backends`
   cmds. Dropping it stops those nodes building the dbi dep. Safe for all
   INHERITS nodes; must NOT drop for `cli` (NEEDS) or the dbi/sabre coverage
   subsets (hermit_unit/hermit_modes) unless that coverage moves to the
   third-party side.
2. **`--workspace`** on `build.workspace`, `lint.clippy`, `doc.doctests`.
   `detcore-dbi`/`detcore-sabre`/`hermit-install` are `members` but not
   `default-members` (they each carry a non-optional `reverie-dbi`/`reverie-sabre`
   dep), so `--workspace` compiles them → DynamoRIO **regardless of the feature
   flag**. `members − default-members = {detcore-dbi, detcore-sabre,
   hermit-install}` exactly. Fix: replace `--workspace` with `--all-targets`
   (uses default-members = product-only) or `--exclude` the three crates.

**Cleanest edit shape (for hermit-perf, who owns the DAG + makespan):**
- `build.workspace`: `cargo build --all-targets` (no `--workspace`, no feature) —
  product-only fat build; the 21 pure nodes reuse it with zero DynamoRIO.
- New/renamed `build.runtime_release` (or a `build.third_party` node) keeps
  `--workspace --all-targets --features third-party-backends`; the NEEDS/MIXED
  nodes (`cli`, `hermit_unit`, `hermit_modes`, `dbi_parity`, `sabre_examples`,
  `liteinst_strict`, `doctests`) depend on it.
- `lint.clippy`: `--exclude` the three crates.

**Makespan note for hermit-perf:** since `build.workspace` is the fan-out root
and makespan is flat from j≥5, moving the DynamoRIO compile off the default path
should shorten the critical path. That measurement is hermit-perf's to take
(VERIFY item 3 on the joint task).

## What is NOT the fix

- Not a `build.rs` bug: line 339 was a generic assert, now a named error (#371).
- Not a Cargo `default-members` gating bug: the exclusion is correct and honored
  for the shipped binary and bare `cargo build`. CI's `--workspace` opting back
  in is deliberate; the defect is that it does so in the *universal fan-out root*.
- Not contention and not a corrupt shared archive (ruled out: solo validate at
  310a3689 failed twice at the same place; cdcf47c9 passed 5/5 concurrently).

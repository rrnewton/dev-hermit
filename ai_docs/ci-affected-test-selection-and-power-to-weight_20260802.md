# CI affected-test selection + power-to-weight (design + prototype)

Date: 2026-08-02
Task: `test-power-to-weight-and-selective-selection` (P1)
Slot: 243 · Branch: `codex/selective-test-selection`
Deliverables (in `rrnewton/hermit`): `ci/select-tests.rs`,
`ci/test-footprints.json`, `ci/power-to-weight.rs`, `ci/test-selection.md`.

## Problem

Every commit — including docs-only and single-backend changes — currently pays
for (nearly) the full ~46-node portable CI DAG. Two wins are available:

1. **CI-irrelevant skip.** A change touching only docs/notes/images should run no
   CI at all.
2. **Selective selection.** A change with a limited footprint (one backend, one
   script, guest fixtures) should run only the nodes it can affect plus their
   build dependencies — not the whole suite.

A third, longer lever is **power-to-weight**: rank nodes by cost vs. value so
expensive, rarely-needed nodes can move off the per-commit critical path.

## Design

### Single source of truth, one added relation

`ci/dag/portable.json` already encodes the node universe, each node's command,
and build-order `deps`. The one thing it does not encode is which *source paths*
feed which nodes. The prototype keeps the DAG authoritative and adds exactly that
relation in a **separate** file, `ci/test-footprints.json`, so it does not
collide with concurrent DAG edits by the CI-DAG owner (portable.json is actively
churned). `select-tests.rs` joins the two at runtime and closes the selected set
over the DAG's `deps`.

### Classification and the three decisions

Each changed file is force_full / footprint-match / ci_irrelevant / unknown; the
run resolves to **skip** (all inert), **selective** (subset + deps + preflight),
or **full** (any force_full or unknown, or an untrusted baseline). Details in
`hermit/ci/test-selection.md`.

### Fail-safe is the whole point

The only decision that runs fewer nodes than a change might need is **skip**, and
skip demands positive proof every file is inert. Every ambiguity — unknown path,
a footprint node absent from the live DAG (schema-drift guard), files not proven
inert — collapses to **full**. So a footprint mistake can only waste time, never
mask a regression. The single exception is the `ci_irrelevant` allowlist, which
is deliberately tight (docs, notes, images, non-workflow `.github/**`; the three
real workflow files are force_full).

### The green-baseline refinement (owner)

Selection is a delta **against a green baseline**: run what the delta can affect,
trust the baseline for the rest. The delta differs by context:

- **GitHub PR** — the PR's own contribution vs the target branch:
  `git diff origin/main...HEAD` (merge-base three-dot). The baseline is
  `origin/main`, which is genuinely green (required checks gate it).
- **Local `validate.sh`** — dirty working copy + commits since the last
  known-green commit: `committed-since-baseline ∪ staged ∪ unstaged ∪ untracked`.

Local selection is only **sound** if that baseline is really green, so:

- **No trustworthy baseline ⇒ full.** With no `--baseline` and no
  `HERMIT_LAST_GREEN_SHA`, the tool returns full. It never skips on an unproven
  baseline.
- **The baseline comes from the validate-run-ledger (237b).** The selector is
  storage-agnostic; it consumes one fact — the slot's last-green SHA — via
  `--baseline` or the env var. Contract posted to both tasks.

**Known blocker.** A robustly-green *local* baseline does not exist yet: full
`validate.sh` cannot exit 0 on a devserver (host-sensitive detcore tests +
DynamoRIO cold-checkout failure, per the `validate-sh-cannot-be-green-on-devserver`
skill). Until fixed, `--since-green` correctly falls back to full locally. The
GitHub path is unaffected.

### Shard/cell projection layer (post-44df2944)

CI no longer runs one job per DAG node. 44df2944 grouped non-e2e nodes into
**shards** (`ci/portable-shards.json`, `debug_shards` + `release_shards`) and runs
e2e as a **cell matrix** (`category × mode × backend`, `ci/expected-e2e-plan.json`,
52 portable cells). `select-tests.rs` now projects its selected node set onto that
real shape (`derive_run_plan`):

- A **shard** runs iff any of its nodes was selected; a release shard's `needs`
  (`dbi`/`aux`) decides whether `build-dbi`/`build-aux` are emitted.
- **Cells** are filtered by **backend affinity** declared in the footprint, not by
  node membership: `e2e_backends:[..]` ⇒ only those backends' cells; `e2e_all:true`
  ⇒ every cell; neither ⇒ no cells. This is per-backend selection — a
  `detcore-dbi/**` change runs the dbi-parity shard + only the 8 dbi cells +
  build-dbi; `detcore-sabre/**` ⇒ sabre shard + 4 sabre cells + build-aux;
  `detcore/**` (core, incl. KVM guest code) ⇒ all 52 cells. force_full/unknown ⇒
  full matrix.
- **KVM** is `unsupported` in the portable plan (no `/dev/kvm`) ⇒ zero portable
  cells; KVM e2e runs in the privileged lane. Its guest code under `detcore/**`
  maps to `e2e_all`, so a KVM-touching core change still runs the full portable
  matrix.

`--format github` emits `shard_matrix` (`{"shards":[..]}`) and `cell_matrix`
(`{"include":[{category,mode,backend,slug}..]}`), both GitHub-Actions
`fromJSON`-consumable, plus `shard_count`/`cell_count`/`build_*`. This is the
wiring contract handed to the `ci` sharding owner.

Verified: `--self-test` = **57/57** (adds ~20 shard/cell/per-backend checks: docs⇒0
shards/0 cells/no build; dbi⇒dbi-parity shard + only dbi cells + build_dbi-not-aux
+ strict subset; sabre⇒sabre shard/cells + build_aux-not-dbi; liteinst⇒liteinst;
core⇒all 52 cells + e2e_all; dbi vs sabre cells disjoint). Real-commit projection:
docs/skills⇒skip (0/11 shards, 0/52 cells); standalone `scripts/*.rs`⇒2/11 shards
(clippy+docs) **0 cells** (precision win — no backend affinity); CI-harness/Cargo/
validate.sh⇒full (11/11, 52/52, all builds).

### Power-to-weight

`power-to-weight.rs` joins cost (`hint.est_duration_s`, **hand-estimated** per
`ci/dag/README`) with a value proxy — selection frequency over a sample of recent
commits, computed by shelling out to `select-tests.rs` (selection logic stays in
one place). Low rate ÷ high cost = nightly candidate. Output is explicit that the
rate is measured on past commits (predictive only if the change mix is stable)
and that a low-power node is ranked, not condemned.

## Prototype validation

Anchors: `hermit` slot-243 branch `codex/selective-test-selection` off
`origin/main` b763fb92; `ci/dag/portable.json` = 46 nodes.

**Selector self-test:** `ci/select-tests.rs --self-test` → **37/37 checks pass**
(glob matcher, all decision branches, force/unknown/inert, merge_delta,
resolve_baseline). No git or network required.

**Real-history behavior** (last 14 commits on `origin/main`, via
`git show --name-only <sha> | ci/select-tests.rs --files -`):

| Commit class | Example | Decision | Nodes |
| --- | --- | --- | --- |
| CI harness change | 44df2944 consolidate release-build chain | full | 46/46 |
| standalone scripts | db749365 -h/--help in scripts/*.rs | selective | 8/46 |
| docs / skills | b763fb92 add testing skills | skip | 0 |
| validate.sh | 95a274b2 usage for -h/--help | full | 46/46 |
| reverie pin (Cargo) | c008e014 | full | 46/46 |
| demo workflow YAML | 714eac8c Demo 7 BTF tooling | skip | 0 |

**Footprint-branch coverage** (synthetic single-file inputs, confirms every
map branch resolves with zero stale nodes):

| Changed file | Decision | Nodes |
| --- | --- | --- |
| `detcore/src/scheduler.rs` (core) | selective | 46/46 |
| `tests/backend-parity/x.c` | selective | 35/46 |
| `detcore-sabre/src/lib.rs` | selective | 18/46 |
| `flaky-tests/src/x.rs` | selective | 15/46 |
| `hermit-verify/src/main.rs` | selective | 10/46 |
| `scripts/stage-liteinst-runtime.sh` | selective | 8/46 |

**Power-to-weight** (`--sample 80`): 26 selective / 14 skip / **40 full** — the
recent window is CI/infra-heavy, so half the commits force full and every node's
selection rate is inflated to 55–82%. Worst power-to-weight nodes (biggest
levers) are the expensive ones: `test.strict_compat` 600s (p2w 0.688),
`build.workspace` 360s, `lint.clippy` 300s. 0 nodes cleared the
nightly-candidate bar (dur ≥ 120s AND rate < 34%) precisely because the CI-heavy
window keeps rates high — a real finding, not a null result: on a
feature-development window the rates would fall and candidates would appear.

## Deferred / follow-ups

- **Live workflow wiring.** Gate `ci-portable.yml`'s matrix on
  `--format github` output. Coordinate with the CI-DAG owner; the local path also
  needs the green-baseline blocker resolved.
- **Measured durations.** Replace hand estimates with observed per-node durations
  once the validate-run-ledger records them.
- **scripts/*.rs precision.** Standalone rust-scripts match `**/*.rs` → pull
  lint/doc; that is safe over-selection but imprecise (workspace clippy/doc do
  not lint standalone scripts). Tighten later if it matters.

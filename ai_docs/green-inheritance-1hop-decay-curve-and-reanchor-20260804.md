# 1-hop green-inheritance: the decay curve and re-anchor crossover

**Task:** `green-inheritance-test-selection-anchored-on-full-main-validates` (P0, owner).
**Date:** 2026-08-04. **Author:** hermit-perf (opus-4.8), consolidating the
paid research previously scattered across 11 task notes (read-only; no code
landed by this artifact).

## Question

The owner mandated **ONE HOP**: an incremental (test-selected) run anchors on a
**full** green, never on another incremental. The selection diff is therefore
`TIP X` vs `ANCHOR Y` (not `X` vs `X-1`), so the saving **decays** as the tip
drifts from the anchor. Deliverable = **the decay curve** and **the derived
re-anchor crossover** (do not pick a commit count; derive the trigger from the
measured saving).

## Answer, one line

On real recent `main`, the 1-hop saving is a **cliff, not a smooth decay**: it is
**zero at essentially every distance** because `force_full`-class paths
(`ci/**`, `Cargo.*`, `validate.sh`, `.github/workflows/*ci*`, `rust-toolchain`)
are touched early and `force_full` is **monotonic in the cumulative diff** — once
any commit in `[anchor, tip]` touches one, the whole window forces full.

## Measured decay curve

- **Anchor Y = `e8a0d8d3`** (a real first-parent ancestor of the then-tip
  `b4e94ce4`, 48 commits back; counted-full pass). Full universe = **47 nodes /
  70 cells / 11 shards**.
- **Cumulative diff `anchor..tip@d`** → decision = **FULL at every d = 1..48**
  (47/47 nodes, 70/70 cells). Cause: commit #1 in the window (`ffb56e24`) touches
  `ci/run-node.sh` ⇒ `force_full`.
- **Product-only projection** (machinery paths removed) still **saturates by
  ~d=3** via reverse-dependency closure over a single touched core file.
- **Contrast — per-commit (`tip` vs its PARENT, the small-diff #1529 case)** over
  the same 48 commits: full **34** / selective **13** / skip **1** ⇒ ~29% of
  commits would save *something* per-commit. This quantifies the owner's point:
  tip-vs-anchor is a much larger diff than tip-vs-parent, and here it is always full.

**Realized 1-hop saving on recent main = ZERO.**

## Derived re-anchor trigger (not a picked N)

Re-run a **full** validate the moment the cumulative window since the anchor
contains its **first `force_full`-class change** (`Cargo.*`, `ci/**`,
`validate.sh`, `.github/workflows/*ci*`, `rust-toolchain`) — from that commit on,
selection yields zero, so keeping the anchor buys nothing.

- In a **CI-machinery-heavy** window that trigger fires ~immediately (d=1 here).
- In a **pure product-dev** window it fires when the union of footprints covers
  the DAG via reverse-dep closure (saturated by ~d=3–6 here).

Cost model for the amortized form: a re-anchor is one full validate (ledger
median **546s**, n=107 full-pass rows) that serves K later selective runs, so
amortized re-anchor cost/commit = 546/K; keep the anchor while
`selective_wall < full_wall − 546/K`.

**Correct trigger signal is wall-based** (`selected_wall / full_wall ≥ θ`), but it
is **blocked**: no per-node wall durations are recorded (ledger stores whole-run
`real_seconds` only). The set-based proxy (`selected_nodes / full_nodes`) is
available now but **optimistic** — full wall is dominated by a few heavy e2e
cells, so a small file selecting one heavy cell reads as set-fraction 0.1 while
wall-fraction is ~0.6. **Shared blocker with power-to-weight: emit per-node wall
into the receipt/ledger.**

## Anchor predicate (verified, bracket-tested)

The anchor must be `profile=full AND result=pass AND selection_mode=full AND
commit_anchored AND !tree_dirty AND executed>0 AND coverage_satisfied` — **not**
"a recent green". This is already a query: `ci-hub validate-status` /
`newest-green` use `is_clean_full_pass` (`ci-hub/lib/validate_status.rs:215`;
`is_clean_full_coverage:145` requires `selection_mode=='full'`).

- **Negative bracket:** pure compat-only SHA `d8e95058` (2× `portable-strict-
  compat-only`, checks=2, pass, no full) ⇒ NOT-VALIDATED, exit 4, qualifying=0,
  disqualified=2.
- **Positive bracket:** counted-full `469a0f92` ⇒ VALIDATED, exit 0,
  qualifying=1, profile=full/full.
- **Ledger hazard confirmed** (n=403 rows): 114 compat-only `pass` rows (of which
  102 have checks==2 — the owner's cited ~92) would fool a `result`-only key; all
  114 correctly excluded by the profile binding. **Usable full-anchor pool ≈ 57
  commits**, not 107: only 59/107 profile=full pass rows carry all schema-5
  binding fields (the rest fail-closed to NotValidated).
- `newest-green` additionally enforces `gate_schema_floor=c369be3f`.

## Footprint mapping is the trust boundary — a live under-selection bug

The selector map is a hybrid: Cargo reverse-dep closure (sound by construction) +
hand-authored `ci/test-footprints-policy.json` for non-Cargo deps; unmapped ⇒
full (fail-safe). **Sound for source paths** (e.g. `detcore/src/scheduler.rs` ⇒
selective 44/45; unknown `.rs` ⇒ full).

**BUG (re-confirmed live at primary main `f80b1c09`, 2026-08-04):**
`ci_irrelevant` contains `.github/**`, but `force_full` names only 3 workflow
files (`ci-portable.yml`, `merge-gate.yml`, `ci-privileged.yml`).
`hermit/.github/workflows/` has **9** files; the other 6 match `.github/**` ⇒
`ci_irrelevant` ⇒ `all_inert=true` ⇒ **Decision::Skip ⇒ ZERO tests**. Verified:

    .github/workflows/ci-dag.yml          => skip   (drives a real matrix — WRONG)
    .github/workflows/validation-levels.yml => skip (drives a real matrix — WRONG)
    .github/workflows/ci-portable.yml     => full   (named in force_full — correct)
    README.md                             => skip   (genuinely inert — correct)
    Cargo.toml                            => full   (force_full class — correct)
    detcore/src/scheduler.rs              => selective (sound map — correct)

This is the fake-green class the mutation test targets, found statically. It is
**LATENT not live today** — selection is not wired to hosted CI (post-#1575) so it
cannot mint a fake green yet; it *would* once wiring lands.

**Fix (precise):** in `ci/test-footprints-policy.json` replace the 3 named
workflow `force_full` entries with `.github/workflows/**` (force_full wins over
ci_irrelevant at `select-tests.rs:388 > 412`), then regenerate
`ci/test-footprints.json` (generator copies both lists verbatim,
`generate-test-footprints.rs:541-542`). `dependabot.yml`/templates stay
`ci_irrelevant`. Add a `self_test` asserting a workflow change ⇒ full, plus one
e2e DOES-fail mutation on the selective path.

## Engineering recommendation

1-hop test-selection yields ≈ZERO on CI-machinery windows (the common case for
recent main) and saves only inside a pure-product-dev window before the first
`force_full` commit. **Pair it with the clean-rebase soft-inherit
(`soft-inherited-validation-across-clean-rebase`, the zero-diff degenerate case
that DOES save), and unblock the wall-based re-anchor trigger by emitting per-node
wall into the ledger.** The green-inheritance verdict must be a **composite**:
`{anchor VALIDATED at/above floor}` AND `{footprint(diff(anchor,tip)) ⊆
selected-tests-that-passed-at-tip}` — `validate-status` computes only the
single-SHA half today.

## Reproduction

Read-only, no checkout needed:

    git diff --name-only ANCHOR..TIP | ./ci/select-tests.rs --files - --format json

Per-file classification: `printf '%s\n' <path> | ./ci/select-tests.rs --files - --format json`.
Selector: `hermit/ci/select-tests.rs` (classify 388>395>412>416). Footprints:
`ci/test-footprints.json` ← `ci/test-footprints-policy.json` ←
`generate-test-footprints.rs`. Ledger: `ignored/validate-run-ledger.jsonl`.

# Proposed stacked topic PRs for the NOT-LANDED pile
Date: 2026-08-06 · Agent: hermit-verify · Task: `coalesce-implemented-pile-into-stacked-topic-prs`
**Proposal only — nothing pushed, nothing landed. herdr-dev owns the landing path.**

## What was stacked, and what could not be
Input is the **NOT-LANDED bucket only** (41), taken from the standing ancestry audit.
LANDED (25) and UNKNOWN (50) are excluded: UNKNOWN is *not* NOT-LANDED — it is research
closing on a durable artifact, and 57 of it was closed that way earlier today.

| | count |
|---|---|
| NOT-LANDED total | 41 |
| …with a **live open PR** → stackable | **16** |
| …**SHA only, no PR ever opened** | **18** |
| …reference a **closed** PR (dead ref) | **7** |

**The headline is not the stacking.** Stacking can help 16 of 41. The single largest
group — 18 tasks — has committed work that was **never published as a PR at all**, so
there is nothing to stack; they need a PR opened before any landing strategy applies.
A further 7 point at closed PRs (the `fix_pr_1147_*` family among them, already shown
stale-premise). Coalescing alone will not drain this pile.

## The two real stacks
Membership is forced by **shared source files**, not by topic. Where the two disagree,
file overlap wins — that is the whole validate-invalidation argument: a validate record
is keyed to a SHA, so two PRs touching one file cannot both keep their evidence through
a serial drain.

### Stack 1 — fixtures · PRs [1673, 1677, 1693, 1704, 1708, 1710, 1728]

Order: ascending PR number (#1673 → #1677 → #1693 → #1704 → #1708 → #1710 → #1728), chronological, minimising rebase churn.

Member tasks:
- `fixture-shared-memory-mmap-coherency`
- `fixture-signal-mask-inheritance`
- `gh-unreachable-direct-route-via-herdr-run`
- `pin-check-ls-remote-must-route-via-herdr-run`
- `randomness-fixture-add-vdso-and-verify-cpuid-case`
- `validate-run-admission-fetch-must-route-via-herdr-run`

File footprint: 28 files. **Collisions that force one stack:**
- `detcore-model/src/config.rs`
- `detcore/tests/misc/mod.rs`
- `hermit-cli/src/bin/hermit/run.rs`
- `hermit-cli/src/metadata.rs`
- `scripts/check-reverie-pin.rs`
- `tests/e2e/manifests/system-utils.toml`

**Derived inventories touched — REGENERATE at assembly, never hand-merge:** `ci/expected-e2e-plan.json`, `tests/e2e/manifests/inventory/test-files.json`

### Stack 2 — tooling · PRs [1725, 1729]

Order: ascending PR number (#1725 → #1729), chronological, minimising rebase churn.

Member tasks:
- `plugin-side-signal-syscalls-actually-routed-to-detcore`

File footprint: 4 files. **Collisions that force one stack:**
- `hermit-cli/src/lib.rs`

## Singletons — no file or mechanism overlap, land individually

| PR | topic | task |
|---|---|---|
| #1730 | fixtures | prove-new-corpus-guests-exercise-each-patching-backend |
| #1719 | fixtures | seven-fixtures-emit-boolean-not-value-structurally-blind |
| #1687 | integrity-guards | validate_service_env_drops |
| #1692 | integrity-guards | fix-verify-strict-compare-info-only |
| #1735 | tooling | validate-harness-detection-refuse-bare-in-dev-hermit |

Stacking these would add rebase risk and buy nothing: with no shared file, a serial
landing does not invalidate its neighbours' validate records.

## Method notes that change the result

**Hub/derived files are excluded from the stacking edges.** A first pass linked seven
PRs through `ci/expected-e2e-plan.json` and `inventory/test-files.json`. Those are
generated inventories that every fixture registration touches, so linking on them
collapses unrelated work into one mega-stack. They are resolved by regenerating at
assembly time, so they are reported per stack but do not create dependencies.

**Closed-on-the-merits PRs cannot re-enter.** The stack input is drawn from currently
OPEN PRs, so the three closed-on-merits PRs are excluded structurally rather than by a
name blocklist that could drift.

**The serial landing queue could not be read from a machine-readable source.**
`ci-hub pr-status --json` exposes `mechanism_overlaps` but no queue field. Every PR in
both stacks is currently a **draft**, which is the best available proxy for *not* being
in the serial queue — stated as a proxy rather than asserted as fact. Confirm against
herdr-dev's queue before assembling.

**Mechanism overlap was folded in as a second stacking signal** (`detlog-record-framing` #1679/#1718, `strict-compat-denominator` #1717/#1727, `vdso-getrandom-determinism` #1713/#1720); none of those pairs is in the NOT-LANDED set,
so they did not alter the stacks, but the same rule should apply when they are.

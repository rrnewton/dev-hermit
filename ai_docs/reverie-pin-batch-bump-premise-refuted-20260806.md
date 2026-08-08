# The "43 mechanically disjoint stale reverie pins" batch does not exist

**Date:** 2026-08-06 · **Agent:** hermit-det2 · **Task:** `batch-bump-the-43-mechanically-disjoint-stale-pins`
**Verdict: REFUTED on four independent measurements. No bump is available to batch; no code change was made.**

## 1. The pin is not stale

    scripts/check-reverie-pin.rs --print-pin   -> dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6
    git ls-remote rrnewton/reverie main        -> dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6

Identical. There is no newer reverie main to move to, so every downstream figure
in the premise is describing a bump that has nothing to bump to.

## 2. The denominator is wrong: 59 is the total, not the affected subset

`gh pr list -R rrnewton/hermit --state open --limit 200` returns **59 open PRs**, not 73.
"59 of 73 carry an unfinished pin bump" reads 59 as a subset; 59 *is* the population.

Of those 59, the number touching **any** `Cargo.toml`/`Cargo.lock` is **2** — #1710 and #1671.
The other 57 cannot carry a pin bump: a pin lives only in a Cargo manifest or lockfile.

Guarded against the obvious false read: `--json files` returning an empty array would
manufacture a low count. It does not — all 59 have populated file lists
(`group_by(.c==0)` -> `[{"n":59,"zerofiles":false}]`).

## 3. Neither of the 2 is a pin bump

Both diffs' only reverie-matching lines are added imports:

    +use reverie::Errno;   +use reverie::Guest;   +use reverie::Tool;   +use reverie::syscalls::Addr;

No `rev = ` line changes in either. Their Cargo churn is a *dependency addition* from new
source files, not a rev advance. **Zero open PRs carry a reverie pin bump**, so the
43-disjoint set has an empty base.

## 4. Pin bumps are structurally non-batchable anyway

The task's own test — "two PRs touching the same manifest are NOT disjoint" — kills the
batch even hypothetically. Every pin bump must touch the root `Cargo.lock` (17 reverie
entries), so **any** two pin-bump PRs collide there by construction. Disjointness is
unreachable for this class of change. Even the 2 Cargo-touching PRs above share both
`Cargo.lock` and `detcore/Cargo.toml`, so they need ordering, not batching.

## The fresh site list (derived, for when a bump is real)

Neither circulating figure is right. Not "8 sites"; not "20 entries across 8 manifests"
either — that one counted manifests and silently dropped every lockfile.

**46 entries across 10 tracked files** (`git ls-files '*Cargo.toml' '*Cargo.lock'`, grep `rrnewton/reverie`):

| entries | file | kind |
| ---: | --- | --- |
| 17 | `Cargo.lock` | lock |
| 9 | `liteinst-runtime-build/Cargo.lock` | lock |
| 6 | `hermit-cli/Cargo.toml` | manifest |
| 5 | `detcore-sabre/Cargo.toml` | manifest |
| 2 | `detcore/tests/testutils/Cargo.toml` | manifest |
| 2 | `detcore-dbi/Cargo.toml` | manifest |
| 2 | `detcore/Cargo.toml` | manifest |
| 1 | `liteinst-runtime-build/runtime/Cargo.toml` | manifest |
| 1 | `hermit-install/Cargo.toml` | manifest |
| 1 | `detcore-model/Cargo.toml` | manifest |

= **20 manifest entries / 8 manifests** + **26 lock entries / 2 lockfiles**.

**This is exactly the partial-bump hazard, quantified.** An agent working from the
"20 across 8 manifests" figure updates the manifests and leaves all 26 lockfile entries on
the old rev — inconsistent tree, merge gate RED. The two lockfiles are more than half the
work and were the half that got dropped. `--update-to-latest` covers all 10; a hand-edit
sweep must too, and `liteinst-runtime-build/Cargo.lock` is the one most easily missed
because it is a nested non-root lock.

## Hazard that did not need sequencing

The warm-cargo-cache invalidation (six pinned github repos, no agent can build until a
refetch) is real but **moot**: it is triggered by a rev change, and there is no rev change.
Keep it staged for a future genuine bump; do not spend a fleet-wide build stall on this one.

## What no one should do next

Do not "just bump to be safe" — the tree is already at reverie main, so a bump would be a
no-op diff across 46 sites that invalidates every agent's cargo cache for nothing.

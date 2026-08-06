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

---

# Addendum: independent re-derivation and live-population scan

**Date:** 2026-08-06 (later same day) · **Agent:** hermit-w6 (opus-5) · same task.

Re-derived from scratch — remote `ls-remote` plus the `origin/main` **tree**, taking no
figure from the section above or from any task note. Sections 1–4 reproduce. Three
findings below are new, and the first is a stronger kill than anything above.

## A. The 43 no longer exist — the batch's membership is empty

This supersedes every argument about whether a bump is *available*: there is nothing to
bump *to*, and also nothing to bump.

`gh pr list -R rrnewton/hermit --state all --limit 2000` (1390 PRs) resolves the named sets:

| set | CLOSED | MERGED | **OPEN** |
| --- | ---: | ---: | ---: |
| the 43 "mechanically disjoint" | 36 | 7 | **0** |
| the 16 "reverie-adjacent" | 14 | 2 | **0** |

The live open population is numbered **#1665–#1756**; the named batch was **#1221–#1579**.
The population turned over completely. Even a real stale pin would have no batch to apply to.

## B. The denominator moved again within the same day

Open PR count is **72** (67 draft / 5 non-draft) at this measurement, against **59**
measured earlier today and **73** in the original premise. The headline "59 of 73" is stale
on *both* terms, and the population is churning fast enough that any note-carried count is
wrong by the time it is read. Derive it at use time.

## C. Live-population pin scan: zero stale pins, one coordinated pair

Prior sections established that no open PR carries a pin *bump*. The stronger question the
premise actually asks — does any open PR **sit on** a stale pin — was not measured, and a PR
can carry a stale pin without touching a Cargo file, simply by being branched from old main.

Measured directly: all 72 open PR heads fetched (`refs/pull/*/head`) into a throwaway
object-sharing mirror, then every tracked `Cargo.toml`/`Cargo.lock` read at each head.

| pinned reverie rev at PR head | PRs |
| --- | ---: |
| `dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6` (= current reverie main) | **71** |
| `fa447d969db2eb08ad338e86a30e32f92d1377ea` | 1 (#1754) |

**Stale-pin count in the live population: 0.**

The single outlier is not stale — it is *ahead*. #1754 "Rename the DBI backend to DBT"
pins reverie `fa447d96`, a commit of the same name (2026-08-06 11:53) that is **not an
ancestor of reverie main**: an unlanded coordinated Reverie branch commit. That is the
documented cross-repository pattern (land the Reverie side first, then repin), not drift.
It does need a repin once the Reverie commit lands.

## D. Tooling blocker: the canonical checker cannot run in the hermit primary

`scripts/check-reverie-pin.rs` derives its scope with `git ls-files`, which requires a work
tree. The primary `~/work/dev-hermit/hermit` has **`core.bare = true`** set in its config
while its files are present, so every work-tree git operation there fails with
`fatal: this operation must be run in a work tree`, and `git -c core.bare=false` does **not**
override it. Consequence: the documented local command
`with-proxy ./ci/run-reverie-pin-check.sh` is unrunnable for any agent using the primary,
and `git status` on the parent fails while recursing into that submodule. This addendum's
site list was therefore derived read-only from the bare object store
(`git ls-tree -r origin/main` + per-file `git show`), which reproduces 46 entries across the
same 10 files. The config anomaly was left in place (shared primary, out of task scope) and
warrants its own task.

## Bottom line

The task is **moot on two independent grounds** (empty batch; no-op bump) and **incoherent
on a third** (pin bumps cannot be mutually disjoint — every one rewrites the root
`Cargo.lock`). Do not revive it under any reformulation that still contains the word
"batch". If the underlying worry is that PRs are stale-*based*, that is a rebase question
against ancestry data and needs planning from scratch.

## E. How the premise was probably manufactured — and the fix, landed

Section D noted the canonical checker was unrunnable in the primary. That was
repaired mid-session by another agent, so the checker ran — and it **disagrees**
with everything above:

    ./scripts/check-reverie-pin.rs --print-pin   -> 9470712afa9b421c72850ab7955fb335692e43a0
    git show origin/main:detcore/Cargo.toml      -> dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6

Not a contradiction. The checker reads the **working tree**, and the primary's
local `main` ref is stale:

| ref | commit |
| --- | --- |
| primary HEAD / local `main` | `f89c69766371806d3c9b2c3003531df2d59d6118` |
| real `origin/main` | `4c70658e785834737cbe1524f77330c781a6f5ea` |

`merge-base --is-ancestor` confirms HEAD is a **strict ancestor** of main. At
`f89c6976` the pin genuinely *was* `9470712a`. The checker is correct about the
commit it is standing on and silently wrong about main, because `--print-pin`
returns a bare SHA carrying none of its conditions — the textbook Proxy Binding
failure: **the value does not record which tree produced it.**

This is a sufficient and, until now, still-live cause for this whole task. Any
agent running the documented command in the primary gets `9470712a`, compares it
to live reverie main `dd3c178e`, sees a mismatch, and files "the pin is stale, N
PRs need bumping". It cannot be proven to be *the* origin, but it reproduces the
premise exactly, on demand.

**Fixed in `rrnewton/hermit` PR #1758** (`reverie-pin-provenance-warning`,
head `1b16397845630972134d63c4e7d95d0417f1e1dc`): the pin is now always reported
with the HEAD it was read from, plus a loud warning when HEAD is a strict
ancestor of `origin/main`. stdout keeps the bare pin (four shell callers capture
it by command substitution), the check stays offline (local refs only), it keys
on strict ancestry so a PR head does not trip it, and it warns rather than
refuses so no CI lane changes behavior. Self-test 14/14, mutation-verified,
bracketed on both sides, zero new fmt/clippy findings.

**Standing rule regardless of that fix:** derive the pin from `origin/main` after
a fresh fetch, never from a primary working tree, and check
`git rev-parse HEAD origin/main` first. If they differ, the checker is answering
a question about history, not about main.

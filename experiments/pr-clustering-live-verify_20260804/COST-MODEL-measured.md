# Cost model of the deep (merge-tree) conflict path — MEASURED

Question raised by the owner: `pr_status`'s real-conflict (`git merge-tree`)
engine is OFF by default, justified as "the expensive fan-out path." Is the
expense actually per-PR, or is it a one-time fetch amortized across all PRs?

## Method

- Host: hermit primary checkout `~/work/dev-hermit/hermit`, `origin/main` at
  `b824a34856a3dec3a46a8fc8698abdfcf917b7fc`, 107 open PRs (#1147–#1575).
- Fetch: `/usr/bin/time -v with-proxy git fetch --all --quiet` (warm — all
  remotes already present).
- Per-PR analysis: `git merge-tree --write-tree --name-only <main> <headSha>`
  over 40 open-PR head SHAs (all commits local after the fetch), wall-timed.

## Results (measured 2026-08-04T00:33Z)

| Quantity                                   | Value                         | Unit             |
|--------------------------------------------|-------------------------------|------------------|
| `git fetch --all` (warm)                   | 2.91                          | s wall           |
| fetch peak RSS                             | 17.8                          | MB               |
| fetch net transfer                         | negligible (refs local)       | —                |
| per-PR `merge-tree` probe                  | 0.0365                        | s wall (local)   |
| N-vs-main over all 107 open PRs (derived)  | ~3.9                          | s wall           |
| full pairwise 107×107 = 4950 probes (der.) | ~181 (~3 min)                 | s wall, one-time |

## Interpretation

The cost being avoided by the off-by-default was **never per-PR**. It is a single
`git fetch` (O(1), ~3s, amortized across ALL PRs); after it, every `merge-tree`
call is a cheap **local** graph op at ~37 ms. A full planning run's deep analysis
is one fetch (~3s) + N-vs-main (~4s), or ~3 min for the exhaustive pairwise
clustering — one time, not per PR.

This is the same defect class as `-j 2` on a 316-core box, a theoretical
footprint overcounting a measured peak, and serialized validates against 754 GiB
of headroom: a **conservative default with no derivation, chosen because
something "seemed expensive," never measured.** The measurement is the argument
to turn the deep path **ON by default in planning runs**.

## Two tiers — neither subsumes the other; run both

1. **TIER 1 — mechanism buckets (NO fetch).** Bucket by config key / flag /
   label / concurrency group / workflow trigger (`tags LIKE '%mechanism:%'`).
   Catches **semantic** collisions merge-tree cannot see: same key, *different*
   files, merges cleanly (e.g. #1567 vs #1575, which nearly landed
   contradicting each other). Owned by agent-utils PR #11 (hermit-ptw).
2. **TIER 2 — deep analysis (ONE fetch).** Real `merge-tree` edges → connected
   components (`cluster_by_conflict`) → stack order within → parallel lanes
   across. Catches **textual** conflicts. This is agent-utils PR #12 (this task,
   SHA 3147ddd).

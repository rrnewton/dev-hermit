# Wall-clock before/after for affected-test selection — measured BEFORE baseline

**Date:** 2026-08-03
**Author:** impl agent, opus-4.8 (task `wallclock-before-after-test-selection`, owner hermit-227b)
**Status of the wiring under study:** PR
[#1529](https://github.com/rrnewton/hermit/pull/1529) (`codex/wire-affected-test-selection`) —
**NOT landed on `main`** as of this writing.

## Bottom line (read this first)

**There are ZERO "after" runs to measure.** Affected-test selection
(`ci/select-tests.rs`, #1500) is merged but *inert*; the job that would consume it
(PR #1529) has not landed. Every `ci-portable.yml` run that has ever executed —
including the 100 green runs analysed here — ran the **full 47-node matrix**. So a
true before/after wall-clock comparison **cannot be produced yet**, and none of the
numbers below should be read as a measured time *saving*.

What this document *can* establish honestly:

1. The **measured full-matrix cost today** ("before"), bucketed by the footprint
   class the selector *would* have assigned each commit — i.e. how expensive the
   commits that selection would have shrunk actually were.
2. That a meaningful fraction of real commits (**42 of 100**) have a reducible
   footprint, so the "after" measurement is worth collecting once #1529 lands.
3. Exactly **what is needed** to obtain the "after" numbers.

The predicted reduction from matrix counts (docs-only ⇒ 0 nodes, scripts-only ⇒ 8
nodes, single-backend DBI ⇒ 14 nodes, vs 47 full) is a **structural prediction, not
a wall-clock measurement**, and is kept out of the headline deliberately
(see "Predicted, not measured" below).

## Method

- **Population:** the 100 most-recent **successful** runs of `ci-portable.yml`
  (`gh run list -R rrnewton/hermit --workflow ci-portable.yml --status success -L 100`).
  Events: 94 `pull_request`, 6 `push`. All predate the selection wiring, so all ran
  the full matrix.
- **Footprint classification (authoritative, tool-grounded):** for each run's head
  commit, the changed file list came from
  `gh api repos/rrnewton/hermit/commits/<sha> --jq '.files[].filename'`, fed to the
  **actual selector** `./ci/select-tests.rs --files "<files>" --format json`. The
  bucket is the tool's own `.decision` (`skip` | `selective` | `full`) and
  `.node_count`. This ties the buckets to the exact logic that will run in CI, not
  to a hand-rolled heuristic. Full baseline `node_count` = 47.
- **Durations from run timestamps** (`gh run list` JSON):
  - `queue_min = (startedAt − createdAt) / 60`
  - `run_min   = (updatedAt − startedAt) / 60`

  Queue and run are reported **separately** because the hosted pool is
  queue-starved (below); conflating them distorts the picture. `run_min` is
  end-to-end workflow wall time (parallel fan-out across ubuntu-latest runners),
  not summed node-work.
- **Precision:** absolute minutes at 0.1 resolution. Sample sizes stated per bucket.
  No value below is extrapolated; each is computed from the 100 observed runs.
- **Raw data:** one JSON object per run (id, event, url, footprint, node_count,
  n_files, queue_min, run_min, title) in `/tmp/portable-runs-classified.jsonl`
  (machine-local; regenerate with the commands above).

## Measured BEFORE baseline — full-matrix cost by would-be footprint bucket

All runs below executed the **full 47-node matrix** (selection not yet live). The
bucket is what the selector *would* have assigned.

| Would-be bucket | Runs (n) | run_min min / median / max / mean | queue_min median / max / mean |
|---|---:|---|---|
| `skip` (0 nodes)          |  5 | 30.1 / 46.5 / 52.0 / 42.8 | 0.0 / 0.0 / 0.0 |
| `selective` (32 or 35 nodes) | 37 | 23.0 / 47.1 / 49.6 / 42.3 | 0.0 / 0.0 / 0.0 |
| `full` (47 nodes)         | 58 | 20.7 / 46.2 / 207.0 / 70.4 | 0.0 / 224.8 / 13.8 |
| **all**                   | 100 | 20.7 / 46.5 / 207.0 / 58.6 | 0.0 / 224.8 / 8.0 |

`selective` node-count split: 28 runs at 35 nodes, 9 runs at 32 nodes (SaBRe-only
backend-affinity changes).

**Reading it:** the `skip` and `selective` buckets — the 42 commits selection would
have shrunk — cost a **median ~46–47 min of run time each** today, purely because
the full matrix runs regardless of footprint. That ~46 min median is the "before"
anchor those buckets would be measured against after landing. The `full` bucket
carries the entire long tail (max run 207 min) and essentially all the queueing.

## Queue starvation is real and must stay separate from run time

- Median queue across all 100 runs is **0.0 min**, but the mean is **8.0 min** and
  **12 of 100 runs queued longer than 30 min** — including one at **224.8 min**
  (~3.7 h) of queue alone. Every one of those 12 long-queue runs is in the `full`
  bucket.
- Because queue time is a property of pool contention, not of the matrix, selection
  reduces *run* time directly and reduces *queue* time only indirectly (fewer/smaller
  jobs competing for runners). The two must be reported as distinct axes; a headline
  that added them would credit selection with queue improvements it does not directly
  cause.

## Why there is no AFTER data, and what is needed to get it

`select` (the new job in `ci-portable.yml`) only shrinks the matrix on a
`pull_request` with a resolvable base and the kill-switch off. It does not exist on
`main` yet. To produce a real before/after:

1. **Land PR #1529.** Until then, every run is full-matrix by construction.
2. **Accumulate green AFTER runs per bucket.** Need a usable sample (target ≥5 green
   runs each) of:
   - **docs-only** PRs (expect `skip`): e.g. a `docs/**` or `*.md`-only change.
   - **scripts-only** PRs (expect `selective`, ~8 nodes): e.g. `scripts/**`.
   - **single-backend** PRs (expect `selective`, ~14 nodes): e.g. `detcore-dbi/**`
     (note: the DBI footprint path is `detcore-dbi/**`, *not* `reverie/reverie-dbi/`).
   - **full** PRs (core/`Cargo.lock`/`tests/**`): the control bucket — must stay
     ~unchanged, confirming selection did not silently drop coverage.
3. **Compare like-for-like within each bucket:** AFTER `run_min` for a docs-only PR
   vs the BEFORE full-matrix `run_min` for docs-only PRs (median ~46.5 min here).
   Never compare a docs-only AFTER run against a full-matrix BEFORE run as a headline.
4. **Keep queue and run separate**, and report sig-figs + sample size per bucket, as
   above.
5. Re-run this exact pipeline (it already classifies by the live tool), splitting the
   population at the #1529 merge SHA into before/after cohorts.

## Predicted, not measured (explicitly out of the headline)

For completeness only: a `skip` decision means the downstream build/test jobs do not
run at all, so the AFTER wall-clock for a docs-only commit is *bounded by* the
`select` + `plan` jobs (checkout + a sub-second selector decision + the shard-coverage
guard) — on the order of a couple of minutes rather than ~46. A `selective` decision
runs a dependency-closed subset (8 or 14 of 47 nodes) plus the one-time build stage.
These are **structural expectations from the DAG**, not observations; the actual
saving depends on which nodes fall on the critical path and on pool contention at run
time, and will only be known from measured AFTER runs. The local
`Centralized test manifest and inventory` check runs in ~6–8 s, consistent with the
selector's decision cost being negligible, but that is a local data point, not a
hosted-CI wall-clock.

## Reproduction

```bash
cd hermit
with-proxy gh run list -R rrnewton/hermit --workflow ci-portable.yml \
  --status success -L 100 \
  --json databaseId,headSha,headBranch,event,createdAt,startedAt,updatedAt,displayTitle,url \
  > /tmp/portable-runs.json
# For each run: classify its head commit by the authoritative selector, then
# queue_min=(startedAt-createdAt)/60, run_min=(updatedAt-startedAt)/60.
for sha in $(jq -r '.[].headSha' /tmp/portable-runs.json); do
  files=$(with-proxy gh api repos/rrnewton/hermit/commits/$sha --jq '.files[].filename' | tr '\n' ' ')
  ./ci/select-tests.rs --files "$files" --format json | jq '{decision, node_count}'
done
```

## Related

- PR #1529 (the wiring under study), which unblocks `adopt-github-merge-queue`.
- `ai_docs/reference/` selector contract; `ci/select-tests.rs --self-test` (57/57).
- Memory: `affected-test-selection-wired-pr1529`,
  `demo-gate-throughput-bottleneck` (landing throughput context).

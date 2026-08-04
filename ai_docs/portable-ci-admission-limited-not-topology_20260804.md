# Portable CI is admission-limited (runner supply), not topology-limited

**Task:** `portable-ci-is-admission-limited-runner-supply-not-topology` (P1, coordinator).
**Date:** 2026-08-04. **Author:** hermit-ghdag (coordinator).
**Builds on:** memory `portable-ci-dead-wall-is-runner-queue-not-graph` (predecessor: the 91% /
6h38m dead wall is GitHub runner QUEUE WAIT, not the `needs:` graph or compute).

## Headline (observable consequence + decision)

Portable CI's dead wall-clock is **runner SUPPLY / admission deprioritization**, established
now by *direct measurement of realized concurrency* rather than by inference from queue-wait
alone. When the shared free pool admits us freely, the DAG **demands 17 concurrent runners**;
when the pool is saturated we **peaked at 8**. 17 > 8 is the whole story: the graph is ready to
run wider than the pool will admit. **The decision this creates:** the durable win is an org
**runner-pool** increase (or a dedicated pool), quantified below — *not* another topology
change. The one free topology change that survived analysis (#1580, fold `reduce-e2e` into
`regular`) is being landed separately and is the last such lever.

## Established (measured) — state the window on every number

| Quantity | Value | Window / provenance |
|---|---|---|
| Free-admission realized peak concurrency | **17 concurrent jobs** | run `30890631572`, WINDOW **2026-08-04T08:07:36→08:32:54Z** (25.3 min wall, LIGHT queue); interval-sweep over per-job started/completed times; 35 total jobs |
| Leaf-antichain upper bound (structural ceiling) | **~26** | derived from the job graph's maximum independent set of runnable leaves (matrices expanded) |
| Saturated-pool observed peak concurrency | **8 concurrent** | predecessor measurement under a saturated pool |
| Per-account concurrency cap (GitHub Free) | **20** | GitHub Free-plan hosted concurrency limit |
| Dead wall under saturation | **93% (398/427 min)** | predecessor, run `30842388041` (see prior memory) |

**Interpretation, bound per *Establish What You Have*:**

- **17 (demand) > 8 (saturated peak)** — measured on the *same* graph — proves the graph is
  ready to run ~2× wider than the saturated pool admits. This is a *realized-concurrency*
  count (jobs simultaneously in `in_progress`), not a queue-wait proxy; it is the demand-side
  number the pool decision needs.
- **8 (peak) < 20 (account cap)** — we never reach our own account ceiling, so the throttle is
  **not** our per-account cap. It is the **undocumented shared-free-pool deprioritization**
  (threshold throttle with a slow, hours-long recovery signature; see
  `github-public-repo-throttle-shape`). Buying account-cap headroom would not help; the
  constraint is upstream of it.
- The **structural ceiling (~26)** is DERIVED from the DAG, not guessed. It bounds what any
  amount of runner supply could ever consume for this graph: past ~26 runners, extra supply
  buys nothing for a single run.

## Cheap lever first (it is ours): demand we may not need to admit

Per the task's ordering, quantify our own admission consumption before asking the org for more.

**Measured per-push admission share** (push at 2026-08-04T08:07Z; `gh run view <id> --json jobs`):

| Workflow | Jobs/push | Share | Cancel on main push? |
|---|---|---|---|
| CI (GitHub-managed portable) | **35** | **~90%** | **NO** (`cancel-in-progress: false`) |
| P0 Demo Gate (hot paths) | 2 | ~5% | NO (per-run_id group ⇒ never cancels) |
| CI (privileged) | 1 | ~3% | NO (`cancel-in-progress: false`) |
| Docs | 1 | ~3% | **YES** (`cancel-in-progress: true`) |
| Merge Gate | 4 | (merge_group event, not push) | only on `pull_request` |

Push-event total ≈ **39 job-admissions**, of which portable alone is **35 (~90%)**.

**The cheap lever, correctly located:** the dominant admission consumer is **portable (35
jobs, ~90%)**, and on a main push it runs to completion even when superseded
(`cancel-in-progress: false`, group `portable-refs/heads/main`). During a landing burst, every
intermediate main commit still spends its full 35-job portable run. Docs (1 job) is the only
push workflow that supersede-cancels — it saves ~3% at most. **So the only demand-reduction
worth the org's attention is on portable itself**, and it is a real trade, not free:
- Enabling supersede-cancel on main portable (or validating only the latest main head) would cut
  the ~90% consumer's burst waste, BUT trades away per-commit post-merge portable coverage and
  interacts with `ci-portable-autoretry.yml` (which re-runs cancelled portable runs) — the
  `cancel-in-progress: false` is deliberate. Quantify the burst-waste (superseded portable runs
  per landing hour × 35) before proposing the flip.
- #1580 removes 1 job from the 35 (fold `reduce-e2e`→`regular`) — a ~3% per-run demand cut on
  the dominant workflow, and it shortens the critical path by one re-queue.
- **#1580** (fold `reduce-e2e` into `regular`, chain depth 5→4) removes one whole job from the
  critical path (`reduce-e2e` did ~6s of work but sat as a full job behind a ~50-min re-queue).
  This is a demand/latency reduction on *our* side and is the last surviving topology lever.

## Refuted — do not re-propose

- **Flattening the `needs:` chain** (inlining prebuilt-artifact edges into ~25 leaves): priced
  at **+5.7h runner-seconds**, which *increases* demand on the scarce pool. The edges guard
  prebuilt artifact trees / are fail-closed gates / are cleanup ordering — they are DATA and
  GATE edges, not artificial serialization. See prior memory + `portable-dag-width-*`.

## The org ask, with numbers

1. **Pool size / admission latency distribution** — request the actual shared-free-pool size
   and the admission-latency distribution for `rrnewton/hermit` (we observe 96–121 min per-job
   waits under saturation; peak 8 concurrent; recovery over hours).
2. **What the DAG would consume if freely admitted** — **17 concurrent** (measured, window
   above), bounded above by **~26** (derived). A dedicated/expanded pool of ~17–26 runners is
   the range that matters; beyond ~26 there is no single-run benefit.
3. **What N more runners buys** — under saturation, wall is dominated by re-queue waits
   (~93% dead). Moving from an 8-admit ceiling toward the 17 the graph wants is the direct
   lever on wall-clock; the exact wall gain should be measured on a dedicated pool, not
   projected from a contended one (an emptier queue fakes a win — always compare same queue
   state).

## Reproduction

- Realized-concurrency sweep: for a run, pull each job's `startedAt`/`completedAt`
  (`gh run view <id> --repo rrnewton/hermit --json jobs`), sweep a +1/−1 event line over the
  timestamps, take the max — that is realized peak concurrency for that window. **Always record
  the window**; a peak without a window is not a measurement, because wall swings ~17× purely
  by queue state (24.6 min empty vs 426.9 min saturated on the *same* graph).
- Structural ceiling: expand the matrix jobs, compute the largest antichain of runnable leaves.

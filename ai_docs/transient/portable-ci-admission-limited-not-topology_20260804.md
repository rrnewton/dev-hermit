# Portable CI: admission-limited under saturation, serial-spine-limited once admitted

**Task:** `portable-ci-is-admission-limited-runner-supply-not-topology` (P1, coordinator).
**Date:** 2026-08-04. **Author:** hermit-ghdag (coordinator).
**Builds on:** memory `portable-ci-dead-wall-is-runner-queue-not-graph` (predecessor: the 91% /
6h38m dead wall is GitHub runner QUEUE WAIT, not the `needs:` graph or compute) and
`two-level-parallelism-outer-times-inner-and-serial-tail` (hermit-220: `strict_compat` serial
tail dominates the validate critical path).

## Headline (observable consequence + decision)

Two different bottlenecks on two different axes — do not conflate them:

1. **DEAD wall (queue wait, ~93% under saturation) is admission-limited.** When the shared free
   pool is saturated we peak at 8 concurrent while the same graph runs 17 concurrent when freely
   admitted — proof by direct measurement that GitHub's shared-free-pool deprioritization, not
   our account cap, is throttling us. The fix is a **dedicated/expanded pool**.
2. **BUSY wall (once admitted) is SERIAL-SPINE-limited, not width-limited.** The peak of 17
   concurrent exists for only **51s = 3.4% of the run's wall**; the DAG spends **66% of its wall
   at ≤2 concurrent**. So more runners buy *only* the compressible fan-out slice, and the busy
   wall floor is set by two incompressible serial segments (a backend build + the strict-compat
   tail) that **no runner count can touch**.

**The decision this creates:** ask the org for a dedicated pool sized to **~8–12 runners** (which
covers 77–86% of the DAG's above-1 concurrency demand) — **NOT** 17–26, because the 17-peak is a
3.4% spike. Then the *remaining* busy-wall lever is not runners at all: it is shrinking or
parallelizing the two serial poles. The one free topology change (#1580, fold `reduce-e2e` into
`regular`) is landing separately and is the last such lever.

## Established (measured) — state the window on every number

All from run `30890631572`, **WINDOW 2026-08-04T08:07:36→08:32:54Z** (25.3 min wall = 1518s,
LIGHT queue, 35 jobs), unless noted. Per-job `startedAt`/`completedAt` via
`gh run view --json jobs`; concurrency by per-second interval sweep.

| Quantity | Value | Provenance |
|---|---|---|
| Realized peak concurrency (free admission) | **17** | interval sweep; held for only **51s (3.4% of wall)** |
| Saturated-pool observed peak | **8** | predecessor, saturated pool |
| Per-account concurrency cap (GitHub Free) | **20** | GitHub Free-plan limit |
| Dead wall under saturation | **93% (398/427 min)** | predecessor, run `30842388041` |
| Wall ≈ critical path this (light) run | 1518s ≈ ~1489s crit-path | pool was already sufficient here → runners bought ≈0 |

**Concurrency-over-time profile** (the key new evidence — how much wall is compressible by more
runners vs incompressible):

| Concurrency | Time at ≥ level | % of wall | Reading |
|---|---|---|---|
| ≥ 1 | 1505s | 99.1% | (whole run) |
| ≥ 2 | 1052s | 69.3% | |
| ≥ 4 | 458s | 30.2% | |
| ≥ 8 | 350s | 23.1% | only this slice wants >8 runners |
| ≥ 12 | 216s | 14.2% | |
| ≥ 17 | **51s** | **3.4%** | the "peak 17" is a brief spike |
| **1..2 (serial spine)** | **1004s** | **66.1%** | **incompressible by any runner count** |

**Incompressible serial spine (concurrency == 1 segments, named):**

| Segment | Solo duration | Job |
|---|---|---|
| build front tail | **320s** | `Build release backends once (dbi + sabre + liteinst)` |
| test tail | **119s** | `test: strict-compat` |

439s of pure solo work, plus the ≤2 band, = 66% of wall no runner count can compress. This
**verifies hermit-220** (strict-compat is a real serial tail: 119s solo at the GitHub-job level
here; ~600s at ~12 cores as the local validate node) and adds a **second, larger** serial pole:
the DBI+SaBRe+LiteInst backend build.

**Interpretation, bound per *Establish What You Have*:**

- **17 > 8 on the same graph** = the pool throttles below what the graph is ready to run — but
  17 is a *peak*, needed 3.4% of the time. As a pool-sizing target it is misleading; the
  demand-weighted target is ~8–12.
- **8 < 20 (account cap)** = the throttle is the undocumented shared-free-pool deprioritization
  (`github-public-repo-throttle-shape`), not our account cap. Account-cap headroom would not help.
- The **~26 leaf-antichain upper bound** is a theoretical width ceiling; the *effective* demand
  is far below it because the graph is serial-spine-shaped, not wide.

## Cheap lever first (it is ours): demand we may not need to admit

**Measured per-push admission share** (push at 2026-08-04T08:07Z; `gh run view --json jobs`):

| Workflow | Jobs/push | Share | Cancel on main push? |
|---|---|---|---|
| CI (GitHub-managed portable) | **35** | **~90%** | **NO** (`cancel-in-progress: false`) |
| P0 Demo Gate (hot paths) | 2 | ~5% | NO (per-run_id group ⇒ never cancels) |
| CI (privileged) | 1 | ~3% | NO (`cancel-in-progress: false`) |
| Docs | 1 | ~3% | **YES** (`cancel-in-progress: true`) |
| Merge Gate | 4 | (merge_group event, not push) | only on `pull_request` |

Push-event total ≈ **39 job-admissions**, portable alone **35 (~90%)**.

**The cheap lever, correctly located:** the dominant consumer is portable (35 jobs, ~90%), and
on a main push it runs superseded commits to completion (`cancel-in-progress: false`, group
`portable-refs/heads/main`). Docs (1 job) is the only push workflow that supersede-cancels — it
saves ~3% at most. So the only demand-reduction worth attention is on portable itself, and it is
a **real trade, not free**:
- Supersede-cancelling main portable (or validating only the latest main head) would cut the
  ~90% consumer's burst waste, but trades away per-commit post-merge coverage and interacts with
  `ci-portable-autoretry.yml` (re-runs cancelled portable). The `cancel-in-progress: false` is
  deliberate.
- #1580 trims 1 of the 35 (fold `reduce-e2e`→`regular`) — ~3% per-run demand cut on the
  dominant workflow, and shortens the critical path by one re-queue.

**Burst-waste, NETTED (was hypothesis; the un-netted upper bound is refuted below).**
WINDOW 2026-08-03T15:06→2026-08-04T09:11 created span (18.1h), 40 CI-portable main runs
(`ignored/portable-main-runs.tsv`; `gh run list --workflow ci-portable.yml --branch main`).
Run outcomes: **32 cancelled, 7 success, 1 in-progress.**

The decisive question is not "how many runs were superseded" but "how much compute did the
superseded ones actually burn." The trap: `wall = updatedAt − createdAt` conflates **queue wait**
with **execution**, and under `cancel-in-progress: false` a *running* portable run is NOT
superseded — so a cancel is almost always a run killed *while still pending in the
`portable-refs/heads/main` concurrency queue*, which burned nothing. Verified by pulling per-job
data:

| Quantity | Value | Provenance |
|---|---|---|
| Cancelled runs with **zero jobs instantiated** (pending → superseded → ~0 compute) | **31 / 32** | `gh run view <id> --json jobs` returns `total:0` (incl. recent runs at 8/10/14/21 min wall) |
| Cancelled runs that actually **executed** | **1 / 32** (`30873193855`) | 35 jobs, `7609` job-seconds ≈ 1.09 full-run-equivalents (autoretry-driven) |
| Netted real burst-waste over the window | **≈ 1 full-run-equivalent / 18h** | not the ~11 (75,652 job-sec) the wall-based upper bound implied |

**Wall-before-cancel is admission queue, not waste.** The 32 cancelled runs' wall spans 0.7–90 min
(median 8.0), but 31 of them ran zero jobs across that whole span — they sat in the concurrency
queue and were auto-superseded. The naive model (`job-seconds = wall × avg-concurrency`, from the
light-run curve) gives ~75,652 job-sec / ~10.8 full runs; that is **refuted** — it wrongly assumes
execution during wall.

**Decision — the cheap lever is REFUTED, not merely small.** Flipping `cancel-in-progress` to
`true` saves ≈0: the superseded runs already burn nothing (pending, and GitHub already cancels
pending-superseded runs regardless of the flag). The flag only governs an *admitted, running* run —
which is exactly the one you want to finish for per-commit post-merge coverage. Do **not** change
it. The real bottleneck is upstream of this entirely: admission (below).

## Refuted — do not re-propose

- **Flattening the `needs:` chain** (inlining prebuilt-artifact edges into ~25 leaves): priced
  at **+5.7h runner-seconds**, which *increases* demand on the scarce pool. The edges are
  DATA / fail-closed GATE / cleanup ordering, not artificial serialization. See prior memory +
  `portable-dag-width-*`.
- **Sizing a dedicated pool to the 17-peak (or ~26 width).** The peak is a 3.4% spike; paying
  for 17–26 runners wastes ~half of them almost always. Size to ~8–12.

## The org ask, with numbers

1. **Pool size** — observed saturated peak **8 concurrent** vs account cap **20** (GitHub Free) ⇒
   the shared free pool throttles us below our own cap. Request a dedicated pool.

   **Admission-latency distribution (MEASURED, job `startedAt − run createdAt`):**

   | Queue state | First job admitted | p50 | max | Window |
   |---|---|---|---|---|
   | Empty (light) | **~3s** | ~3s | ~12s | run `30890631572`, 08-04 08:07Z |
   | Saturated | **1318s (22 min)** | **2161s (36 min)** | **3087s (51 min)** | run `30873193855`, 08-04 02:54Z, 35 jobs |

   Admission latency swings **~3s → 51 min purely by queue state** on the same graph. This latency
   *is* the dead wall: the saturated run's 51.5 min wall was ~22 min before its first job even
   started, then jobs trickled in through minute 51 as the pool admitted them. (Earlier
   predecessor figure of 96–121 min per-job waits is a heavier-saturation point on this same
   distribution.)
2. **What the DAG would actually consume if freely admitted** — a brief spike to 17 (3.4% of
   wall) over a spine that sits at ≤2 concurrent for 66% of wall. **Demand-weighted, ~8–12
   concurrent captures 77–86% of all above-1 demand.** Size the dedicated pool to ~8–12, not the
   peak.
3. **What N more runners buys (wall-time, concrete)** — under SATURATION the win is admission:
   the measured contended run spent **~22 min before its first job** and stretched to **51.5 min**
   as jobs trickled in. A dedicated pool of ~8–12 admits all 35 jobs near-immediately, collapsing
   wall toward the **~25 min critical path** — roughly a **2×** wall cut for that run, and
   elimination of the multi-hour dead-wall under heavy saturation. Once admitted, the BUSY wall
   floor is the serial spine (backend build 320s + strict-compat 119s solo = 439s, plus the ≤2
   band = 66% of wall); **no runner count cuts it**. In the light-queue run wall ≈ critical path,
   so runners there bought ≈0. Beyond ~8–12 runners the marginal wall gain is ≈0. Measure the wall
   gain on a dedicated pool, never projected from a contended one (an emptier queue fakes a win —
   always compare same queue state).
4. **The residual busy-wall lever is not runners** — it is shrinking/parallelizing the two serial
   poles: the DBI+SaBRe+LiteInst backend build and the strict-compat tail. That is a separate
   workstream from the pool ask.

## Reproduction

- Concurrency profile: `gh run view <id> --repo rrnewton/hermit --json jobs`, take each job's
  `startedAt`/`completedAt`, sweep per-second `+1/-1`, then histogram time-at-each-level and
  extract concurrency==1 segments with the job name. **Always record the window** — wall swings
  ~17× purely by queue state (24.6 min empty vs 426.9 min saturated on the *same* graph), so a
  peak without a window is not a measurement.
- Data for this analysis: `ignored/1580-run-jobs.tsv` (run 30890631572 per-job timing).

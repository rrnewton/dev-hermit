# Drain reporting as two pools

Task: `report-the-drain-as-two-pools-cleanup-backlog-vs-steady-state-flow`

## Outcome

The drain report must answer two different questions without blending their
denominators:

1. **`cleanup-backlog`: is the finite, old cohort shrinking?**
2. **`steady-state-flow`: are new PRs landing at least as fast as they arrive?**

The age split is necessary but not sufficient. Recomputing “all open PRs older
than 24 hours” on every tick creates a moving target: fresh PRs that are not
served age into that count and can hide progress on the original cleanup job.
The cleanup cohort therefore has to be frozen once. New work that exceeds the
24-hour service target remains in the steady-state cohort and is reported as
`flow-debt`; it never silently becomes historical cleanup.

## Preserve the two existing measurements without mixing them

Two locally recorded snapshots use different units and times:

| Snapshot | Unit | Cleanup/stale | Fresh/flow | Rate evidence |
|---|---|---:|---:|---|
| Owner framing recorded by this task | implemented-unlanded task record | 70 | 61 | 44 new task records in 6 h = 7.33 task records/h |
| PR snapshot at approximately 2026-08-04T21:30Z | open GitHub PR | 68 | 24 | see PR rate table below |

The first snapshot established the owner’s operational distinction, but “each
task is approximately one PR” is not an identity join. It cannot be subtracted
from a PR merge rate. The report’s canonical throughput unit should be one PR
number in one repository. Task coverage is a separate join-quality field:
`tasks_with_pr`, `tasks_without_pr`, and `prs_without_task`.

The previously measured PR-level rates were:

| Trailing window | Hermit merged | Reverie merged | Combined merge rate | Combined created | Combined creation rate |
|---|---:|---:|---:|---:|---:|
| 6 h | 16 | 2 | 18/6 = 3.00 PR/h | 18 | 18/6 = 3.00 PR/h |
| 24 h | 36 | 20 | 56/24 = 2.33 PR/h | 64 | 64/24 = 2.67 PR/h |

Source: the 2026-08-04T21:26Z local TaskGraph note on
`pr-planning-process-consolidation-drain-as-testcase`. Its method counted
GitHub PRs by `mergedAt` and `createdAt`, separately for `rrnewton/hermit` and
`rrnewton/reverie`, then summed equal windows. This task performed no network
refresh, so these numbers are a dated example, not a current-state claim.

The six-hour total capacity was exactly consumed by new flow: `3.00 - 3.00 =
0.00 PR/h` upper-bound spare. The 24-hour window was unstable: `2.33 - 2.67 =
-0.34 PR/h`. Even before attributing individual merges to a pool, total merge
capacity left no measured surplus for cleanup. At zero surplus, a cleanup ETA
is undefined rather than “slow.”

## Membership contract

Choose and persist a baseline instant `t0` in UTC. Let `cutoff = t0 - 24h`.
Use half-open time intervals throughout.

### `cleanup-backlog` — frozen stock

At `t0`, freeze the exact `(repo, pr_number, head_sha)` members satisfying:

```text
state == OPEN && createdAt < cutoff
```

The baseline list is immutable. It can change only through an explicitly
reported data correction, never because the clock advanced. On each tick every
member has exactly one disposition:

- `open`: still consumes cleanup stock;
- `merged`: removed by a PR landing;
- `closed-already-upstream`: removed because typed evidence proves the change
  is already on main;
- `closed-obsolete` or `closed-duplicate`: removed without claiming a landing;
- `reopened`: returned to the open remainder.

Report drafts as an axis inside this pool, not as a third pool. Age alone does
not prove abandonment. Use factual subcounts such as `draft`, `ready`,
`ownerless`, and `no_activity_24h`; call something abandoned only if the report
also publishes the predicate that established it.

### `steady-state-flow` — moving cohort

This pool contains:

- PRs open at `t0` with `createdAt >= cutoff`; and
- every PR created at or after `t0`.

A member that remains open for more than 24 hours becomes `flow-debt` but stays
in this cohort. That counter is the alarm that the steady-state process is
failing its service target. Moving it into cleanup would make the flow look
healthier and contaminate the finite backlog.

Rebases, head updates, draft-to-ready transitions, and reopenings are events on
an existing PR, not new arrivals. A replacement PR is a new arrival; link it to
the superseded PR so non-merge closure is visible.

## Measurement contract

Every report must archive the complete PR records used to derive it and carry:

```text
schema_version
as_of_utc
baseline_as_of_utc
age_boundary_hours = 24
window_hours = [6, 24]
repositories
source_snapshot_path
source_snapshot_sha256
```

The source record needs at least `repo`, `number`, `state`, `isDraft`,
`createdAt`, `updatedAt`, `mergedAt`, `closedAt`, `headRefOid`, and `url`.
When egress returns, collect `--state all`, not only currently open PRs; a
current-open snapshot cannot reconstruct merges or non-merge closures. Archive
the raw response before deriving the report. Report repositories separately,
then combine only identical windows and units.

For a trailing window `[t-W, t)`:

```text
arrivals_W       = count(createdAt in [t-W, t))
landings_W       = count(mergedAt  in [t-W, t))
nonmerge_closes_W= count(closedAt  in [t-W, t) and mergedAt is null)
arrival_rate     = arrivals_W / W
landing_rate     = landings_W / W
flow_wip_delta   = arrivals_W - flow_landings_W - flow_nonmerge_closes_W
service_ratio    = flow_landing_rate / arrival_rate
```

Keep `nonmerge_closes_W` visible. It shrinks WIP, but it is not landing
throughput. A staging or aggregate PR counts as one landing; constituent PRs
closed because their changes are already upstream are individually counted as
`closed-already-upstream`, not relabeled as merged.

### Cleanup metrics

```text
cleanup_baseline
cleanup_open_remaining
cleanup_merged_since_baseline
cleanup_closed_nonmerge_since_baseline, by reason
cleanup_reopened
cleanup_delta_since_prior_tick
cleanup_delta_since_baseline
cleanup_burn_rate, over stated 6h and 24h windows
```

The direct answer to “is the backlog down?” is:

```text
cleanup_open_remaining < cleanup_open_remaining_at_prior_tick
```

Always print the numerator, denominator, and delta, for example `54/68
remaining; -5 since prior tick; -14 since baseline`. A newly recomputed
“currently older than 24 hours” count does not answer this question.

### Flow metrics

```text
flow_open_wip
flow_fresh_open_age_le_24h
flow_debt_open_age_gt_24h
arrivals, landings, nonmerge closures, and rates for 6h and 24h
creation-to-merge median and p95 for landed flow PRs
service_ratio and flow_wip_delta
```

The flow is balanced only when `flow_landing_rate >= arrival_rate` over the
declared window. A six-hour window reacts quickly; the 24-hour window prevents
one burst from masquerading as sustained balance. Print both rather than
selecting the favorable one.

## Capacity split

Attribute every merged PR to its frozen pool membership:

```text
mu_total   = mu_flow + mu_cleanup
flow_margin = mu_flow - lambda_arrival
```

To hold steady state, reserve at least `lambda_arrival` PR/h of demonstrated
landing capacity for flow. The demonstrated cleanup capacity is `mu_cleanup`;
the optimistic fungible upper bound is:

```text
cleanup_spare_upper_bound = max(0, mu_total - lambda_arrival)
```

If the upper bound is zero, cleanup cannot drain without allowing fresh WIP to
grow or throttling production. For cleanup baseline `B` and desired horizon
`H` hours, the required sustained capacity is:

```text
required_cleanup_rate = B / H
required_total_rate   = lambda_arrival + B / H
```

Using the dated PR snapshot (`B=68`, `lambda=3.00 PR/h`), a 72-hour cleanup
would require `68/72 = 0.94 PR/h` dedicated to cleanup and at least `3.94 PR/h`
total. The measured six-hour total was only `3.00 PR/h`, so the report must say
“insufficient measured capacity,” not assign a fictional percentage split.

Operationally:

1. If `mu_flow < lambda_arrival`, stabilize flow or enforce a WIP limit; every
   cleanup landing otherwise increases fresh debt.
2. If `mu_flow >= lambda_arrival`, keep the demonstrated flow reserve and
   allocate the measured remainder to cleanup.
3. Apply `fresh-flow` from the consolidated PR-planning process to the moving
   cohort: rebase, validate, review, and land without waiting to form clusters.
4. Apply `stale-drain` to the frozen cleanup cohort: stable conflict clusters
   may use the typed staging-batch design, and clusters land as they ripen.

The pools share landing machinery but have different objective functions.
Flow optimizes sustained service rate and age; cleanup optimizes shrinkage of a
fixed set.

## Required human report

Each status update should use this fixed shape:

```text
DRAIN TWO-POOL REPORT @ <as_of UTC>
Baseline: <t0>; source: <snapshot path>@sha256:<hash>

CLEANUP-BACKLOG (frozen cohort)
  remaining: <open>/<baseline> (<delta prior>; <delta baseline>)
  retired: merged=<n>, already-upstream=<n>, obsolete/duplicate=<n>
  shape: draft=<n>, ready=<n>, ownerless=<n>
  burn: <n>/h over 6h; <n>/h over 24h; ETA=<hours|undefined>

STEADY-STATE-FLOW
  6h: arrivals=<n> (<rate>/h), landed=<n> (<rate>/h),
      nonmerge-closed=<n>, margin=<rate>/h
  24h: arrivals=<n> (<rate>/h), landed=<n> (<rate>/h),
       nonmerge-closed=<n>, margin=<rate>/h
  WIP: fresh=<n>, flow-debt(>24h)=<n>; lead-time p50=<h>, p95=<h>

CAPACITY DECISION
  measured total=<rate>/h; flow reserve=<rate>/h;
  cleanup demonstrated=<rate>/h; required for <H>h goal=<rate>/h
  decision: <hold-flow-and-drain | throttle-production | accept-flow-growth>

DATA QUALITY
  task<->PR join: tasks_without_pr=<n>, prs_without_task=<n>
  corrections/missing timestamps: <explicit list>
```

Never lead with a single combined open count. It can rise while cleanup is
working, fall because work was closed unmerged, or stay flat while equal arrival
and landing rates conceal zero cleanup capacity.

## Implementation boundary

This artifact specifies the report and its arithmetic. It does not refresh
GitHub, mutate PRs, execute a drain, or claim that the dated snapshot is live.
Execution requires egress to archive a new `--state all` PR snapshot. The
existing consolidated process already owns the `fresh-flow` and `stale-drain`
procedures; this contract supplies the pool membership and success metrics that
select and evaluate those modes.

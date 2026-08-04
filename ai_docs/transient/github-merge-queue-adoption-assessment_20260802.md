# GitHub merge-queue adoption assessment — rrnewton/hermit (2026-08-02)

Author: hermit-ci. Question (owner, 2026-08-02): #1460/#1476 landed WITHOUT a
green required check and WITHOUT a `locally-validated` stamp — an `admin --merge`
bypassed a failing Merge Gate. Why aren't we on an actual GitHub merge queue,
which would structurally prevent landing-into-red and gate-bypass?

## TL;DR

A GitHub merge queue admits a PR only when the **required** status check(s) go
green on a synthetic "queue commit" (the PR's changes rebased onto the current
branch tip). For rrnewton/hermit that is impossible today for **three**
independent reasons, each of which must be fixed first:

1. **No `merge_group` trigger.** None of the authoritative workflows
   (`ci-portable.yml`, `merge-gate.yml`) fire on the `merge_group` event, so
   required checks would never run on the queue commit → the queue stalls every
   PR forever. **HARD blocker, cheap fix.**
2. **merge-gate FALSE-RED taint.** `merge-gate` keys on the whole
   `ci-portable.yml` run conclusion, which the `reverie-pin` job sinks to
   `failure` on any stale pin even when the authoritative "Regular tests
   (GitHub-managed portable)" job passed. A required check that red-fires on
   good PRs makes the queue evict everything. This is exactly what forced the
   #1460/#1476 admin-merges. **HARD blocker.**
3. **CI too slow for a queue.** The authoritative `regular` job runs the full
   46-node portable DAG serially on one runner (~32 min wall; historically
   quoted ~45 min). A merge queue serializes: each admitted PR re-runs the full
   required check on its queue commit. At 32–45 min/PR with a 200+ PR backlog
   the queue is unusable. **THROUGHPUT blocker → needs the DAG parallelization
   (ci-dag-parallelize-sub10min).**

Until all three are resolved, "adopt the merge queue" cannot succeed; the current
`locally-validated` + post-facto-review + occasional `admin --merge` is the
documented interim.

## Current topology (as of main ef4a524b)

Required/authoritative path:
- Branch protection requires the `merge-gate` check.
- `merge-gate.yml` re-derives pass/fail by querying the latest
  `ci-portable.yml` **run** for the PR head SHA and requiring
  `status:conclusion == completed:success` (merge-gate.yml:328–342), OR a
  `locally-validated` label (merge-gate.yml:318–325), plus a P0 demo gate for
  hot-path PRs (demo-hot-path.yml, merge-gate.yml:354–372).
- `ci-portable.yml` ("CI (GitHub-managed portable)") runs on `push:main`,
  `pull_request` (all paths), `workflow_dispatch`. Jobs: `reverie-pin`
  ("Reverie pin is current") + `regular` ("Regular tests (GitHub-managed
  portable)", the authoritative 46-node serial DAG).

Non-required lanes:
- `ci-portable-fanout.yml` ("CI (portable, per-cell fan-out)") — PRs scoped to
  `ci/**`, `tests/e2e/**`, `scripts/**`, plus the fanout workflow file itself.
- `ci-portable-parallel.yml` ("CI (portable, parallel fan-out)") — NEW; the full
  DAG parallelization (build-once → per-shard test/lint/doc jobs, each JOB
  <10 min). Same path scope as fanout. Superset of fanout (whole DAG incl. the
  same audited e2e matrix). Non-required; validating via workflow_dispatch.
- `ci-dag.yml` ("CI (DAG runner, manual)") — workflow_dispatch only.
- Self-hosted PMU lane — non-blocking (main unprotected; not a required check).

## Prerequisites, owners, and sequencing

Ordered so each step is safe on its own:

### P1 — merge-gate FALSE-RED de-taint  (owner: coord; patch ready)
Key `merge-gate` on the authoritative **job** ("Regular tests (GitHub-managed
portable)") instead of the whole `ci-portable.yml` run, OR move `reverie-pin`
into its own non-required workflow so the run conclusion == the authoritative
result. Ready-to-apply patch: `benchmarks/ci-dag-portable_20260802/HANDOFF-patches-for-coord.md`
(Patch 2). This alone stops the admin-merge pattern for stale-pin PRs and is a
prerequisite for the queue (a queue needs a required check that only goes red on
real failures). Reverie-pin stays enforced as its own signal.

### P2 — CI throughput: promote the parallel DAG lane  (owner: hermit-ci → owner/admin for the swap)
Bake in `ci-portable-parallel.yml` (each JOB <10 min) until it is reliably green
on real PRs, then swap the authoritative required check from the serial
`regular` job to the parallel lane and retire the serial job. The required-check
swap is a **branch-protection change (owner/admin) + a `merge-gate.yml` edit
(coord)**; hermit-ci stages and validates it but does not own branch protection.
Interim de-duplication (this sweep): retire `ci-portable-fanout.yml` once the
parallel lane is green (parallel is its strict superset) — removes the
double/triple-CI on CI-infra PRs.

### P3 — add `merge_group` triggers  (owner: hermit-ci; cheap, do with the swap)
Add `on: merge_group:` to the authoritative workflow(s) so required checks run
on the queue's synthetic commit. Without this the queue never gets a signal.
This should land together with (or immediately before) enabling the queue, and
be tested with a throwaway PR.

### P4 — enable the queue + define the required set  (owner/admin only)
In repo settings: enable "Merge queue" on `main`; set branch protection to
require exactly the fast authoritative check (post-swap) and NOT the flaky
self-hosted PMU lane; keep "Require branches to be up to date" satisfied by the
queue's rebase. Then stop using `admin --merge` except for true emergencies.

## Why the queue is worth it here

- Structurally prevents landing-into-red: a PR cannot merge unless its changes
  pass required CI **rebased onto current main**, killing the "green on stale
  base, red after merge" class that #1460/#1476 fell into.
- Removes the up-to-date-branch chase (queue rebases for you).
- Removes the human `admin --merge` bypass as routine practice.

## Blocking dependency graph

```
merge-queue-enabled (P4, owner/admin)
  <- merge_group triggers (P3, hermit-ci)
  <- fast required check (P2: promote parallel DAG; owner/admin swap + coord merge-gate edit)
       <- ci-portable-parallel proven green on real PRs (hermit-ci)
       <- retire ci-portable-fanout (hermit-ci; de-dup)
  <- merge-gate de-taint (P1, coord; patch ready)
```

## Status of the pieces (main ef4a524b)

- P1 de-taint: patch prepared, NOT landed (coord owns the landing gate).
- P2 parallel lane: landed additive/non-required (eeebff09); live validation in
  progress; fanout retirement pending green.
- P3 merge_group triggers: not started (cheap; do at swap time).
- P4 enable queue: owner/admin, after P1–P3.

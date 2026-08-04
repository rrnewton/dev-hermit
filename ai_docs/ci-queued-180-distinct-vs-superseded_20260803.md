# The 180 queued hermit runs: distinct work, not superseded same-branch commits

**Question (owner, 2026-08-03):** of the ~180 QUEUED `rrnewton/hermit` runs, how many are
DISTINCT PRs vs multiple queued commits from the SAME branch? Distinct PRs, distinct
branches, distribution of queued-runs-per-branch, top offenders.

**Snapshot:** `gh run list -R rrnewton/hermit --status queued --limit 300` at ~18:56Z
(≈1h after the owner's `ci-hub history` read). Returned **168** still-queued runs (his 180;
the queue drifts as runs start/cancel). Directionally identical.

## Answer, numbers first

| metric | value |
|---|---|
| queued runs | 168 |
| distinct branches | **93** (92 feature/PR branches + `main`) |
| distinct head SHAs | 122 |
| runs on feature branches | 124 across 92 branches (avg 1.35/branch) |
| runs on `main` | **44** (26% of the whole queue) |
| **true superseded older-commit runs (feature branches)** | **8** |

**Runs-per-branch distribution:** `main`=44 · 32 branches=2 · 60 branches=1.
Of the 32 two-run feature branches: **24 are the SAME SHA running two different workflows**
(Demo Gate + Portable — by design, not superseded); only **8 branches carry a distinct
older SHA** (the real supersede case, = 8 runs).

### The hypothesis is REFUTED
The dispatch hypothesis was "most of the 180 are superseded commits on the same branch →
fix with per-ref `concurrency: cancel-in-progress`." **False.** Only **8 of 168 (5%)** are
true feature-branch supersedes. The queue is **overwhelmingly DISTINCT work** — 92 distinct
PR branches each queuing 1–2 legitimate runs, plus `main`.

## The real shape: two capacity bottlenecks on two different runner pools

By workflow (with age of the still-queued runs):

| workflow | runs | median age | max | >1h | runner |
|---|---|---|---|---|---|
| CI (GitHub-managed portable) | 91 | 4.3h | 5.4h | 85 | `ubuntu-latest` (GitHub-hosted) |
| P0 Demo Gate (Hermit hot paths) | 51 | 4.3h | 6.1h | 47 | **single self-hosted `pmu-serial`** |
| Portable auto-retry on cancellation | 23 | 0.5h | 0.9h | 0 | transient, fine |

The owner's `median 0 / max 7.4h` is exactly this bimodal shape: `median 0` is over ALL
3757 runs (most start instantly); these 168 ARE the stuck tail, each ~4h old.

**A — Demo Gate (51 runs) = single self-hosted `pmu-serial` runner.** Job
`P0 demo gate (demos 1-8)` targets labels `['Linux','X64','hermit','pmu','pmu-serial']`.
All 51 contend for the ONE pmu-serial runner → serialize → 4–6h. This is the known
single-PMU-runner bottleneck (memory: `ci-capacity-single-pmu-runner-bottleneck`).
**19 of the 51 are on `main`, across 19 distinct main SHAs** (every main push fires a Demo
Gate that never gets the runner).

**B — Portable CI (91 runs) = GitHub-hosted `ubuntu-latest` account-concurrency
saturation.** Sampled run 30818220504: **31/32 jobs completed; 1 trailing job
("Reduce e2e parity archives", `ubuntu-latest`) queued.** The portable jobs run and
complete; the run sits "queued" waiting for a trailing aggregation job behind the
account-wide GitHub-hosted concurrent-job cap, saturated by ~90 simultaneous PR runs ×
~30 jobs each.

## What each fix actually buys

**Per-ref `concurrency: cancel-in-progress` removes ~26 of 168 (~15%), NOT the 180:**
- feature-branch supersedes: 8 cancelled;
- `main` stale Demo Gate: 19 → 1 (cancel ~18).

It is **worth adding** — `main` is the #1 offender and its 19 stale Demo Gate runs collapse
cleanly — but it does **not** solve the queue. The dominant 47 stuck Demo Gate + 85 stuck
Portable runs are **distinct work bottlenecked on capacity/admission**, which cancellation
cannot touch.

**The queue-clearing fixes are:**
1. **Demo Gate throughput** (single pmu-serial runner): don't fire Demo Gate on *both* every
   PR *and* every main push; add a second pmu runner; or gate Demo Gate to trusted/landing
   events only. This is the biggest lever on the hours-long tail.
2. **Portable admission control**: cap concurrent PR CI (an account-level concurrency group,
   or fewer/heavier jobs) so ~90 simultaneous fan-outs stop saturating the GitHub-hosted
   pool and starving trailing aggregation jobs.

## Merge-gate interaction (required caution)
Per-ref cancel-in-progress is **safe** against the merge-gate's either/or leg
(locally-validated stamp OR GH-action green):
- **PR refs:** cancelling an OLD commit's run when a NEW commit is pushed is safe — you would
  never land the old commit, so its gate result is moot; the new head re-runs and produces the
  gate. Do NOT cancel across a landing of the *current* head (inherent; re-run on new head).
- **`main`:** main Demo Gate runs are *post-land* health, not a pre-land gate; cancel-in-progress
  keeps the newest main run, so the latest main result still exists for submodule-bump /
  queue-health evidence. Do not blanket-cancel a specific main run that a bump is actively
  citing as evidence.

## NOT the same as two already-measured phenomena (do not conflate)
- hermit-ptw's **gate** serialization (78 jobs / 617 service-s in a 600s window) — a different
  job's burst on one runner, not "168 sitting queued."
- reverie's **Merge Gate** `ubuntu-latest` 27m p90 — anomalous GitHub-hosted congestion, a
  third, separate problem.

## Reproduce
```
with-proxy gh run list -R rrnewton/hermit --status queued --limit 300 \
  --json databaseId,headBranch,headSha,event,createdAt,workflowName,status
# group by headBranch; classify multi-run branches by distinct headSha;
# inspect a Demo Gate run's jobs -> pmu-serial labels; a portable run -> ubuntu-latest.
```
Raw snapshot: `scratch/ghdag-poll/hermit-queued.json`.

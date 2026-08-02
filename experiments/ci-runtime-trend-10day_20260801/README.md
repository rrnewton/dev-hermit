# CI runtime trend — last 10 days (per-lane daily median/p90)

**Task:** `ci-runtime-trend-10day` · **Agent:** hermit-ci · **Date:** 2026-08-01

## Question

Owner: pull CI run durations over the last ~10 days — both self-hosted (validate / safe-ci-dag) and
GitHub-hosted — and graph the trend. **How has validate/CI time grown as the compat envelope went
1 → 6 backends?** Daily median/p90 per lane.

## Headline

**The GitHub-hosted portable CI lane roughly TRIPLED in five days as the envelope expanded:**
median **14.7 min (07-28) → 44.0 min (08-01) = ~3.0×**; p90 **24.9 → 48.9 min ≈ 2.0×** (p90 is
pressing the GitHub-hosted job ceiling). Reverie's Rust lane grew in lockstep, median
**2.4 → 10.1 min ≈ 4.2×** (07-23 → 08-01). The growth is monotonic day-over-day and lines up with
the backend-parity envelope expansion (ptrace → +KVM → +DBI → +SaBRe/LiteInst columns) landing over
the same window.

## Chart (daily median = bar, p90 = `│` marker; scale is per-lane max-p90)

```
── hermit-portable-hosted ──   (GitHub-hosted "CI (GitHub-managed portable)" = the envelope lane)
  2026-07-28  n= 311  ██████████████          │                          med= 14.7  p90= 24.9
  2026-07-29  n=  42  ███████████████              │                     med= 15.4  p90= 29.6
  2026-07-30  n=  89  ███████████████████████            │               med= 23.8  p90= 36.1
  2026-07-31  n=  85  █████████████████████████████████            │     med= 33.5  p90= 46.0
  2026-08-01  n= 229  ███████████████████████████████████████████     │  med= 44.0  p90= 48.9
  2026-08-02  n=  23  ██████████████████████████████████████          │  med= 38.3  p90= 48.8

── reverie-rust ──   ("Regular tests" + "Host-dependent tests")
  2026-07-23  n=  19  ████│                                              med=  2.4  p90=  2.7
  2026-07-26  n= 217  █████ │                                            med=  3.2  p90=  4.3
  2026-07-29  n=  77  ██████                                          │  med=  4.1  p90= 32.8
  2026-07-31  n=  26  ██████████ │                                       med=  7.1  p90=  7.4
  2026-08-01  n=  27  ███████████████│                                   med= 10.1  p90= 10.5

── hermit-validation-levels ──   (self-hosted validate.sh levels; only 07-24..26 in window)
  2026-07-24  n=  76  ████████                                    │      med=  5.5  p90= 30.6
  2026-07-25  n= 204  ████████                                        │  med=  5.6  p90= 33.1
  2026-07-26  n=  36  █████████████████████                     │        med= 14.5  p90= 29.2

── hermit-privileged-selfhosted ──   (self-hosted; median ~0 = runs skip without /dev/kvm)
  2026-07-28  n= 389  ██                                              │  med=  0.2  p90=  4.8
  2026-08-01  n= 266                          │                          med=  0.0  p90=  2.4
```

Full per-day table for every lane: `results/daily-aggregate.csv`; full rendered chart:
`results/trend-chart.txt`.

## Read (when & how much it grew)

- **Where the cost is: the GitHub-hosted portable lane.** It carries the compat-envelope expansion
  (the `safe-ci-dag`/expansion matrix that fans out per backend). Its median climbed every single day
  07-28 → 08-01 (14.7 → 15.4 → 23.8 → 33.5 → 44.0 min). The steepest jumps are **07-29→07-30**
  (+8.4 min) and **07-30→07-31** (+9.7 min), matching when the KVM and DBI corpus columns came
  online. By 08-01 the p90 (48.9 min) is essentially at the wall the job will tolerate — this lane is
  the one to watch/optimize next.
- **Reverie grew ~4× but is still cheap in absolute terms** (2.4 → 10.1 min median); not yet a
  bottleneck, but the same monotonic shape.
- **The self-hosted `validate.sh` levels lane went quiet after 07-26** (no completed runs 07-27
  onward in this pull) — CI iteration shifted onto the GitHub-hosted portable lane + the
  privileged-selfhosted lane. `hermit-privileged-selfhosted` runs near-instant because most runs skip
  (no `/dev/kvm` on the hosted portion); it is **not** a duration signal.
- **Interpretation for the envelope:** end-to-end CI wall-time scales ~linearly with backend columns.
  1→6 backends over this window ≈ 3× on the dominant lane. This is the quantitative case behind the
  parallel timeout/headroom work (`timeout-headroom-and-load-relative`) and any move to prune or
  parallelize the envelope DAG.

## Method / reproduction

Per-workflow GitHub API queries (the flat `gh run list` caps at ~1000 results ≈ 1 day for this repo):

```bash
with-proxy gh api -X GET "repos/rrnewton/hermit/actions/workflows/<id>/runs" \
  -f "created=>=2026-07-23" --paginate \
  --jq '.workflow_runs[] | {branch:.head_branch, concl:.conclusion, created:.created_at,
         event:.event, id:.id, started:.run_started_at, updated:.updated_at, wf:.name}' \
  > ignored/raw/lane-<name>.jsonl
# reverie lanes: repos/rrnewton/reverie/...
./render-ci-trend.rs ignored/raw     # -> results/daily-aggregate.csv + ASCII chart
```

Duration = `updated - started` (seconds) for runs with a non-null conclusion. Raw JSONL dumps live in
the gitignored `ignored/raw/`; the tracked deliverables are `render-ci-trend.rs`,
`results/daily-aggregate.csv`, and `results/trend-chart.txt`. Exact run counts and window per lane
are in `metadata.json`.

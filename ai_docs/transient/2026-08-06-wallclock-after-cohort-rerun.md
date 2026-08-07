# Wall-clock before/after for affected-test selection — the AFTER cohort, measured

**Date:** 2026-08-06
**Author:** impl agent, opus-5 (task `wallclock-after-cohort-rerun`, agent hermit-w28)
**Supersedes the "no AFTER data" status of:** `ai_docs/transient/2026-08-03-wallclock-before-after-test-selection.md`
**Cohort split SHA:** `be7e26cd3c10de3d76b8d0c2e43e3eabe493aa10` (#1529, "Wire affected-test
selection into portable CI and validate.sh"), committed **2026-08-03T14:16:13Z**, verified
ancestor of hermit `origin/main`.

## Bottom line

**The realized hosted-CI saving from affected-test selection is ZERO — 0 jobs skipped in
46 of 46 measurable AFTER runs — and the reason on record is incomplete.**

The AFTER cohort *does* exist and *is* measurable (contrary to
`affected-test-selection-unreachable-on-hosted-ci-post-1575`, which is right about the outcome
but overstates the unmeasurability). Selection ran. It just never shrank anything, because
**`ci/select-tests.rs` crashed on every hosted invocation**:

```
select-tests.rs failed; falling back to full:
/usr/bin/env: ‘rust-script’: No such file or directory
selection: FULL matrix (reason: select-tests.rs error)
```

46/46 runs, identical line. The selector did not *decide* full — it could not execute, and the
fail-safe correctly fell back to full.

## Cohort definition and denominators

Source: ci-hub history store `ignored/ci-hub/gha-runs.csv`, ingested 2026-08-07T04:12Z.
Store coverage `2026-08-02T00:00:44Z .. 2026-08-07T04:12:07Z` — **the BEFORE side is truncated
at the 08-02 store boundary; this is not all history.**
Workflow `CI (GitHub-managed portable)`, 979 rows.

| Cohort | all conclusions | green | green + `pull_request` | green + PR + full shape |
|---|---:|---:|---:|---:|
| BEFORE (`created_at` < split) | 452 | 100 | 97 | **60** |
| AFTER (`created_at` ≥ split)  | 527 | 179 | 46 | **46** |

Green BEFORE n=100 independently reproduces the original doc's population of 100.

**Only `event=pull_request` can reach selection**, so the AFTER side's other 133 green runs
(104 `workflow_dispatch`, 29 `push`) are structurally ineligible and are *not* pooled in.
45 of the 46 green AFTER PR runs descend from `be7e26cd` by git ancestry, so the workflow file
they executed genuinely contained the `select` job.

**Shape matching (why 97 → 60).** 37 of 97 BEFORE green PR runs have only 1–2 jobs — a
short-circuit workflow shape, not a full matrix. Comparing those against 35-job AFTER runs would
be comparing unequal cohorts, so they are excluded. Matched denominators are **60 vs 46**.

## The reachability window

`#1575` (`d5fcdbe8`, 2026-08-04T00:18:35Z) removed the `pull_request` trigger **10.0 h after**
#1529 landed. That window is the entire population in which hosted selection was ever reachable:
168 ci-portable runs (128 `pull_request`, 40 `push`), 47 green. Exactly 1 green PR run exists
after #1575 — a legacy branch predating it. No *new* hosted run can be selective.

## Measured result — matched cohorts

**Summed job-work (runner-minutes) — the contamination-proof axis:**

| Cohort | n | min | median | p95 | max | mean |
|---|---:|---:|---:|---:|---:|---:|
| BEFORE | 60 | 108.6 | **117.0** | 127.6 | 133.6 | 118.2 |
| AFTER  | 46 | 109.8 | **114.4** | 120.3 | 122.0 | 114.7 |

Median delta **−2.6 runner-min (−2.2 %)**.

**This −2.2 % is NOT a selection saving.** Two facts refute that reading: skipped jobs = 0 in
46/46 AFTER runs, and median job count went *up* (34 → 35). It is node-level workflow drift.
The honest figure attributable to selection is **0.0 runner-minutes, n=46/46**.

## End-to-end wall is confounded — reported, not headlined

| Cohort | n | min | median | p95 | max |
|---|---:|---:|---:|---:|---:|
| BEFORE | 60 | 20.7 | 314.9 | 673.8 | 700.7 |
| AFTER  | 46 | 210.7 | 568.1 | 686.8 | 1121.3 |

Do not read +253 min as a regression. The AFTER window *is* the PR-trigger flood that #1575 was
created to stop. Direct evidence from run `30822321994`: the **earliest** job started +103.6 min
after `run_started_at` and the latest +598.9 min, while total job-work was ~114 min. The wall is
per-job queueing for a saturated hosted pool, not compute. The two cohorts are not exchangeable
on this axis.

## Data defect found: `run_s` is not wall time

The store's `run_s = updated_at − run_started_at` is **invalid for any run later re-touched**.
It yields an AFTER-PR median of 565.6 min against the original doc's 46.5 min from the same
formula. The top AFTER runs share an `updated_at` clustered at 2026-08-04T01:51–01:57Z
regardless of start time (run `30822475239` started 14:23:39Z, "updated" 01:55:22Z) — a bulk
last-touched event. Taken at face value it would have reported selection making CI ~10× slower.
Wall must be derived from job-level `completed_at`, as done here.

## Consequence for a recorded decision

The owner declined two levers on 2026-08-04, one being *restore a `pull_request` trigger*.
That lever alone **would not have produced any saving**: two independent sufficient causes are
stacked — (1) the trigger removal, and (2) the selector cannot execute on the hosted image.
Anyone revisiting this must fix both. The recorded rationale captures only (1).

The failure mode is the safe one: selection degraded to *full* coverage, never to reduced
coverage. No test was silently skipped.

## Reproduction

```bash
# cohorts + denominators
python3 - <<'EOF'   # filter gha-runs.csv on repo/workflow/conclusion/event, split at
                    # created_at vs 2026-08-03T14:16:13Z
EOF
# per-run truth (do NOT use run_s):
gh api repos/rrnewton/hermit/actions/runs/<id>/jobs?per_page=100
#   wall = max(job.completed_at) - run.run_started_at ; work = sum(job.completed_at-started_at)
# selector decision, per run:
gh api repos/rrnewton/hermit/actions/jobs/<select_job_id>/logs | grep 'selection:'
```

## Related

- Split SHA `be7e26cd` (#1529); trigger removal `d5fcdbe8` (#1575).
- Memory `affected-test-selection-unreachable-on-hosted-ci-post-1575` — outcome correct,
  unmeasurability claim too strong, and root cause (2) absent.
- Memory `affected-test-selection-measured-yield` — 14–22 % *structural* yield; this document
  measures *realized* hosted yield at 0 %.

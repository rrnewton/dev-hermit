# Close the observation gap before optimising green-time

**Task:** `close-the-observation-gap-before-optimising-green-time` (P1, Phase-2 prereq).
**Date:** 2026-08-04. **Author:** hermit-ghdag (coordinator).
**Predecessor:** hermit-243 measured green-time is gap-dominated (0.88% portable / 7.99% merge-gate)
and reframed it as *mostly no-signal, not mostly red*. This artifact adds the **definition** of green
(grounded in existing code, not invented), **characterises the gap into its buckets with measured
sizes**, resolves the **portable-vs-merge-gate** decision, and states the **denominator honestly**.

## Headline (the decision this creates)

You cannot optimise "% time green on main" yet, for **two independent reasons that must not be
conflated**:

1. **Observation gap** — hermit main green-time is **0.79%** green, but the rest is **54% GAP
   (no run covers that wall-time) + 14% NO_RESULT (dispatched, cancelled, no verdict) + 31% RED**.
   The largest bucket is **GAP**, whose fix is **dispatch coverage, not test reliability**.
2. **Definition gap** — the green-time metric defines green as GitHub `conclusion == success`, which
   is **blind to the ledger's `executed`/`filtered`/`profile` fields**. It would score all three of
   tonight's false-greens as green. The correct definition already exists in code
   (`is_clean_full_pass`) but green-time does not use it, it is missing a `filtered` clause, and the
   **live ledger writer does not even emit the fields it needs**.

**Do not publish a bare green% as health.** Publish it beside the gap fraction (the tool already
does this) and only after the green side is ledger-verified and the largest gap bucket is closed.

## Measured — state the window on every number

`ci-hub/history/query.py green-time`, definition-date 2026-08-04, run 2026-08-04 ~10:0xZ:

| Repo (authoritative wf) | Window | GREEN | RED | NO_RESULT | GAP | runs by conclusion |
|---|---|---|---|---|---|---|
| hermit (`CI (GitHub-managed portable)`) | since 08-02 00:10Z, 57.75h, 137 commits | **0.79%** (0.46h) | 31.01% (17.91h) | 13.87% (8.01h) | **54.32%** (31.37h) | cancelled **108**, failure 22, success 6 |
| hermit (`Merge Gate`) | same 57.76h | 16.6% (9.59h) | 0% | 0% | **83.4%** (48.17h) | success 125, failure 1 |
| reverie (`Rust`) | since 07-21 22:05Z, 323.83h, 301 commits | **80.05%** (259.23h) | 11.13% | 0% | 8.81% | success 262, failure 37, cancelled 1 |

Daily trend (portable): 08-02 green 0% / red 59% / gap 41%; 08-03 green 1.9% / red 16% / no_result 8%
/ gap 74%; 08-04 green 0% / no_result 62% / gap 38%. Volatile and gap-dominated every day.

## The four buckets, characterised — and the fix per bucket

The taxonomy is **already built** (`query.py:53-152`, four states green/red/no_result/gap + a
seven-case discriminator table at `query.py:83-104`). It is not invented here. Denominator =
**wall-clock**: each commit "reigns" from its first run-creation to the next commit's, and each slice
takes a combined verdict (`query.py:696-799`), precedence `red > gap > no_result > green`.

| Bucket | hermit portable | Root cause | Correct fix |
|---|---|---|---|
| **GAP** | **54%** | No authoritative run covers that wall-time (case 6: absent/pending) | **Dispatch coverage** — every landed commit needs a run that reaches a verdict |
| **NO_RESULT** | 14% | Run dispatched but cancelled below cap, no verdict (case 3) | Same root as gap: **supersede-cancel** (see admission finding) |
| **RED** | 31% | Real failures + `case-7` job-failed-under-cancel (2 runs) | Separate genuine red from partial/filtered via the ledger definition |
| **GREEN** | 0.79% | `conclusion == success` — **unverified against the 3 false-green modes** | Require the ledger predicate (below) |

**This is the same defect shape as everything else this cycle:** a metric reporting one state while
conflating two underlying facts (failing vs unobserved). The cure is the same — separate the buckets
and report them separately.

## How the admission finding feeds this (capacity → signal)

From `portable-ci-is-admission-limited…`: portable is **90% of per-push admissions**, runs with
`cancel-in-progress: false`. The green-time data confirms the consequence directly: **108 of 136
(79%) dispatched hermit main runs are CANCELLED.** That is the supersede burn, and it *is* the
NO_RESULT bucket plus much of GAP — most main commits never reach a verdict.

Two refinements that matter for the fix:
- **`cancel-in-progress: false` does NOT buy per-commit coverage here — it prevents it.** Runs are
  cancelled before they produce a verdict, so commits land unobserved.
- The netted burst-waste finding showed **31 of 32 sampled cancels never started a job** (pending,
  ~0 compute). So the wasted resource is **dispatch/queue coverage, not compute** — which is exactly
  why the largest bucket's fix is coverage (carry-forward verdicts, or admit more main runs
  concurrently), not runner compute and not flakiness work.

## Define green — grounded in existing code, not invented

The task says use the 3-field ledger as the definition. **That definition already exists** as
`is_clean_full_pass` (`ci-hub/lib/validate_status.rs:118-122`), over `HistoryRow`
(`ci-hub/lib/records.rs:94-158`, which carries `executed_tests`, `filtered_tests`, `full_coverage`):

```rust
is_clean_full_coverage(row, sha)          // commit==sha && commit_anchored && !tree_dirty
                                          //   && selection_mode=="full" && profile=="full"
    && row.result.as_deref() == Some("pass")
    && row.executed_tests != Some(0)
```

Against the three known false-greens:

| False-green | Rejected today? | By what |
|---|---|---|
| (a) partial profile (`portable-strict-compat-only`, 2 gates) | **Yes** | `profile == "full"` |
| (b) `running 0 tests` → `test result: ok` | **Yes** | `executed_tests != Some(0)` |
| (c) `1 passed, 154 filtered` | **NO** | there is **no `filtered_tests` clause** anywhere |

**Three required fixes so this definition can actually be the metric:**

1. **Add a `filtered_tests` clause.** Case (c) is ungated today; `nonzero_result.py:118-120`
   explicitly treats filtered>0 as a pass. A full-selection/full-profile row with `filtered_tests >
   0` passes `is_clean_full_pass`. Add `filtered_tests == Some(0)` (or `== expected`).
2. **Fix the live ledger writer.** `hermit/validate.sh:912 append_validation_ledger()` emits
   schema-3 **without** `executed_tests`/`filtered_tests`/`full_coverage`; only the reconstruction
   path `ci-hub/validate/aggregate.py:365-368` produces them. **Proof:** the newest live ledger row
   (`ignored/validate-run-ledger.jsonl`) is `profile: "portable-strict-compat-only"`,
   `result: "pass"`, `checks: 2`, and has none of the three fields. So today most rows cannot be
   judged by the definition at all. (`validate_status.rs:114-116` already notes the field is absent
   "in every validate.sh ledger row today.")
3. **Wire green-time to the ledger.** `green-time` keys purely on GitHub `conclusion in
   {success,neutral}` from `gha-runs.csv` (`query.py:48-51,818-855`) and **never reads the ledger**.
   The metric and the real-green predicate are **two disconnected systems**. GitHub `conclusion` is
   blind to test-counts (a job that ran 0 tests and exited 0 is `success`), so green-time cannot
   reject (b)/(c) without joining to a per-commit ledger receipt. Recommended: keep the
   wall-clock/gap picture from `conclusion` (the only continuous signal) but **annotate each green
   interval as ledger-corroborated vs conclusion-only**, and report those as separate sub-buckets —
   the same separate-the-buckets cure.

## Portable vs merge-gate (the blocking owner decision)

hermit-243 rightly refused to silently default. Reasoned recommendation:

- **Portable is the correct main-HEALTH authoritative** — it re-tests main post-merge. Its low green
  (0.79%) is real signal about coverage, not noise.
- **Merge-gate is NOT a main-health metric — it is a landing-gate audit, and near-circular.** It
  runs only on `merge_group` events (at landing), so it covers little wall-time (83% gap) and by
  construction ~every landed commit passed it (125/126 success). "16.6% green" mostly means "we only
  observe at merge moments." Publish it **separately** as a landing-gate pass-rate, never as main
  health.

The 9× difference (0.79% vs 16.6%) is entirely this coverage-window artifact, not a health
disagreement.

## Denominator honesty — reverie has no receipt writer

Today both repos' green-time comes from **GitHub conclusion**, so reverie's 80% and hermit's 0.79%
are directly comparable and both legitimate. **The trap is forward-looking:** the moment green-time
is upgraded to the ledger definition (required to reject false-greens), reverie collapses to ~0% —
**not because reverie is unhealthy but because `reverie/validate.sh` has no ledger writer.** Verified:
`hermit/validate.sh` has `append_validation_ledger()`; `reverie/validate.sh` exists but contains zero
`ledger`/`receipt`/`jsonl` writes; reverie rows exist only if `aggregate.py` reconstructs them from
raw logs (`aggregate.py:392-404`), and the aggregate.py header comment claiming reverie writes
receipts is **aspirational/inaccurate**.

**Rule:** do not publish a unified ledger-based green-time that counts reverie NOT-VALIDATED as red —
that measures our tooling gap, not our health. Either (a) build the reverie receipt writer first, or
(b) keep reverie on conclusion-based green-time and **label the two repos' signals differently** until
the writer exists.

## Ordered plan to close the gap (then optimise)

1. **Fix the live ledger writer** to emit executed/filtered/full_coverage (blocks everything —
   without it the definition is inapplicable to live rows).
2. **Add the `filtered_tests` clause** to `is_clean_full_pass` (closes false-green (c)).
3. **Close the largest observation bucket (GAP 54%) = dispatch coverage** — carry-forward a verdict
   across commits with no independent run, and/or admit more main portable runs concurrently
   (admission finding). Do **not** treat this as flakiness.
4. **Join green-time to the ledger** and split green into ledger-corroborated vs conclusion-only.
5. **Build the reverie receipt writer** (or label reverie's signal separately) — denominator honesty.
6. **Only then** wire `tick-hub` to run `query.py green-time --append-log` hourly (the owner's
   "measure and log, stay healthy" ask — **not yet wired**; tick-hub today runs
   `operational_health.py github-main`, the current-tip check, not the green-time trend) and publish
   the metric with its gap fraction beside it.

## Reproduction / evidence anchors

- `python3 ci-hub/history/query.py green-time --repo <r> [--workflow <wf>] [--trend day]
  [--append-log]`. Store: `ignored/ci-hub/gha-runs.csv`.
- Ledger: `ignored/validate-run-ledger.jsonl` (path const `validate_status.rs:50`; writer
  `hermit/validate.sh:912`). Definition `validate_status.rs:118-122`; schema `records.rs:94-158`;
  aggregator verdict `aggregate.py:330-351`.
- Taxonomy: `query.py:53-152` (four states + seven-case table `:83-104`).
- Admission/burst-waste feed: `ai_docs/portable-ci-admission-limited-not-topology_20260804.md`.

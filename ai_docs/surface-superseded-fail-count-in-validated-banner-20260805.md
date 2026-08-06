# Surfacing superseded failures in the VALIDATED banner — implemented, planted, and a measured gap in the spec

**Task:** `surface_superseded_fail_count` · hermit-clone (opus-5), 2026-08-05
**Local ci-hub only, no egress, no validate-run.** Parent files; task-authorized.

## What shipped

| change | file |
|---|---|
| `Assessment.failed_records` — count of genuine clean-full-coverage FAILs | `ci-hub/lib/validate_status.rs` |
| `Assessment.withheld_nonpass_records` — clean-full non-pass records the classifier deliberately withholds | same |
| VALIDATED banner: loud `WARNING` line when `failed_records > 0` | `ci-hub/ci-hub.rs` |
| VALIDATED banner: `NOTE` line when `withheld_nonpass_records > 0` | same |
| `failed_record_count` + `withheld_nonpass_record_count` in `--json` | same |
| FAILED banner count corrected (was over-counting) | same |
| 9 new Rust brackets | `validate_status.rs` tests |

Rust suite: **117 passed** (108 baseline + 9 new), 0 failed.

## The plant — a genuine superseded FAIL now shows

Planted a commit with an earlier clean full FAIL and a later clean full PASS (the ledger does not
latch, so the PASS supersedes and the verdict is genuinely VALIDATED):

```
# validate VALIDATED d6e3607b… (passed 2026-08-04T10:26:10Z, wall 178s, host devbig014, profile full/full) …
# validate WARNING d6e3607b… -- 1 SUPERSEDED clean full-coverage FAIL record(s) on this same commit;
  the green above did not disprove them. Same commit passing AND failing a clean full run is a FLAKE
  SIGNAL: investigate before trusting this green for landing.
```

**The plant had to be hardened once, which is itself the finding below.** My first planted FAIL was
copied from a real PASS row and did not carry `dag_jobs` / `concurrent_validates` /
`known_flaky_failure` / `solo_rerun_confirmation`. The classifier refused to call it a failure
(NEEDS-RERUN: "red evidence lacks complete solo execution conditions"), and the banner stayed
silent — correctly. Only after adding those conditions did it count.

## The measured gap: as literally specified, the feature has ZERO live yield

Running the new counters over the real ledger (585 rows, 241 distinct commits):

```
VALIDATED commits: 105
  WARNING (genuine superseded FAIL): 0
  NOTE    (withheld non-pass record): 20
  silent  (no adverse record at all): 85    <- 81% of greens unchanged
```

**20 of 105 VALIDATED commits carry a same-commit `result: "fail"` record — and not one of them is
counted as a failure.** 41 such records exist; every one is withheld. Broken down by *why*:

| count | reason withheld | correct? |
|---|---|---|
| 32 | clean-full, but a schema-3 producer that predates the solo-condition fields → NEEDS-RERUN | yes |
| 5 | clean-full with conditions, but recorded under concurrency (`concurrent_validates` 2–9) → contended | yes |
| 4 | not clean-full (`portable-strict-compat-only`, `only-portable` profiles) | yes |

Each exclusion is deliberate and right: a contended or condition-less red carries no product verdict.
I verified the 5 contended cases individually rather than assuming — they are real product-looking
reds (`portable CI DAG lane`, exit 1 after 261–423s, `known_flaky=false`) withheld purely because
other validates were running. **My counter is not under-reporting; the classifier is withholding.**

But the consequence is that a banner reporting only `failed_records` **would have said nothing about
any of the 20**, which is the exact hole the task set out to close.

## Why I added a second line (beyond the literal spec — easy to drop)

Withholding a record from the *failure count* is not the same as withholding it from the *operator*.
`"1 non-pass record (withheld: contended)"` and silence are different claims, and today silence is
what all 20 get. So the VALIDATED banner also prints:

```
# validate NOTE 893991ac… -- 2 same-commit clean full-coverage NON-PASS record(s) exist but are
  WITHHELD from the failure count (contended, incomplete solo conditions, truncated, or environment
  fault); they carry no product verdict, but this green is not the only record of this commit.
```

This is **my addition, not the task's wording**, flagged so it can be removed with one deletion if
unwanted. It is calibrated, not noisy: **81% of greens (85/105) print nothing new**, and the two
counters never double-count the same record (bracketed by
`a_genuine_failure_is_counted_as_a_failure_not_withheld`).

## Brackets (both directions, 9 tests)

- genuine superseded FAIL counted (`1`), and counted as **N** not a boolean (`3` for three fails)
- **positive control**: a clean green reports `0` and prints nothing
- an environment fault (command-not-found storm) is **not** counted — a green must not be alarmed on
- a failure on a *different* commit is not attributed here
- a contended red is withheld from failures but **still counted** as withheld
- a clean green has nothing withheld either
- the two counters are disjoint
- FAILED-banner count is genuine failures only: with 1 real fail + 1 env fault, `disqualified.len()`
  is 2 but the reported number is now **1**

## Incidental fix

The FAILED banner printed `assessment.disqualified.len()` — every non-qualifying row, including
subset, dirty, truncated and env-fault records. It overstated how many genuine clean full failures
existed. Now uses `failed_records`. Flagged rather than buried because it changes a number an
operator may have been reading.

## Verification and attribution

Rust: **117/117**. Python across `ci-hub/tests` + `ci-hub/validate`: 248 passed, 3 failed — **none
attributable to this change**, each checked rather than assumed:

- `test_failure_evidence::test_measured_flake_is_bound_to_failed_cell` — pre-existing; imports none
  of the touched modules and fails identically with the files reverted to `HEAD`.
- `test_documented_commands::test_repository_inventory_is_complete_and_classified` — complains about
  `./ci-hub/bin/reconcile-receipts`, added by another agent's commit `9cf7819` and documented in
  `ci-hub/README.md`, which I did not touch.
- `test_operational_bounds` (`local-history` subtest) — **passes when re-run alone**; it was
  contention from my own concurrent full-suite run, not a defect.

## Reproduction

```
cd ci-hub && rust-script --test ci-hub.rs                       # 117 passed
./ci-hub/ci-hub validate-status --sha d6e3607ba5096771392c48badbed2f5d9c869538 \
    --ledger scratch/superseded-plant/planted.jsonl             # WARNING line
./ci-hub/ci-hub validate-status --sha 893991acc90a              # NOTE line (real data)
./ci-hub/ci-hub validate-status --sha f9f11510dfd499282f8cef581d07dd2c0882bfc0   # neither
```

# Compatibility envelope: tier and relaxation reclassification

**Published:** 2026-08-07T06:24:49Z
**Host:** `devbig014.atn7.facebook.com`, kernel
`6.18.39-0_fbk0_hardened_0_ga43d5727b443`
**Publication main:** `rrnewton/dev-hermit`
`614644bc829adb22a365535357abaaed5405ab0d`
**Last relevant tier/evidence change:**
`f1e08b25ce732c8464140531008fbc361b00791d`
**Staged relaxation provenance:** PR #81
`2bcdf2de946938f3e71a302c7df0bf884698d333`, paired with the PR #79
relaxation consumer at `d064fb86fe5123dc0d666616c1e1e9a36c6db9b1`

## Result: the drop is the point

These are the same historical rows under two definitions. The new column does
not show a product regression; it stops relaxed or unqualified observations
from reading as strict greens.

| Population | Old definition: bare `deterministic=1` field | After requiring `relaxation_set=[]` (still unevidenced) | After requiring comparison evidence | New combined strict comparison green |
| --- | ---: | ---: | ---: | ---: |
| Canonical scorecard | **346/618** | **2/618** | **0/618** | **0/618** |
| Full-corpus scorecard | **926/1,200** | **5/1,200** | **0/1,200** | **0/1,200** |
| Reverie scorecard | **12/12** | **12/12** | **0/12** | **0/12** |
| e9patch scorecard | **454/454** | **0/454** | **0/454** | **0/454** |
| **All four scorecards** | **1,738/2,284** | **19/2,284** | **0/2,284** | **0/2,284** |

The relaxation definition alone drops the canonical field count
**346/618 -> 2/618** and the four-file count
**1,738/2,284 -> 19/2,284**. Those 19 are not greens: all 19/19 still lack
evidence that a comparison happened. Main `f1e08b2` now refuses such a positive
unless `parity_comparator` or `compared_log_messages` is nonblank, reducing the
evidence-backed count to **0/2,284**. In fact, all 1,738/1,738 old positives
lack both evidence fields. The zero is a refusal for zero qualifying evidence,
not a measurement that 2,284 programs failed determinism.

The combined strict-comparison count is lower again: **0/2,284**. All
2,284/2,284 historical rows are explicitly `comparison_tier=legacy-unqualified`,
so none met either qualifying cross-backend standard:

- `full-stdout-info-stack-heap`; or
- `stdout-info-stack-heap-spot-check`.

There are 1,837/2,284 raw `outcome=pass` rows. Thus the equivalent pass-based
statement is **0/1,837 raw passes qualify as a strict-comparison green**. This
uses the raw-pass denominator; the table above consistently uses all rows.

## Definition change

The **old** number counted a row whenever its historical
`deterministic` field was `1`. That admitted stripped, uncounted comparisons
and did not bind the verdict to the determinism relaxations used by the run.

The **new interim** definition requires all three:

1. nonblank evidence that a comparison actually happened
   (`parity_comparator` or `compared_log_messages`);
2. a valid `relaxation_set` receipt equal to the empty JSON array `[]`; and
3. for a combined strict-comparison green, one of the two qualifying
   `comparison_tier` values above.

A missing, malformed, duplicate, unknown, or non-empty relaxation receipt
cannot authorize `deterministic=1`. Likewise, `legacy-unqualified` is an
explicit non-green tier, not a default.

## Relaxation distribution

| Population | Proven no relaxation | One or more relaxations | Unknown provenance |
| --- | ---: | ---: | ---: |
| Canonical scorecard | **3/618** | **615/618** | **0/618** |
| Full-corpus scorecard | **6/1,200** | **1,194/1,200** | **0/1,200** |
| Reverie scorecard | **12/12** | **0/12** | **0/12** |
| e9patch scorecard | **0/454** | **454/454** | **0/454** |
| **All four scorecards** | **21/2,284** | **2,263/2,284** | **0/2,284** |

The exact sets are:

- 1,637/2,284 rows: `no-virtualize-cpuid` plus
  `max-timeslice=disabled`;
- 65/2,284 rows: those two plus `tmp=/tmp`;
- 107/2,284 rows: `max-timeslice=disabled` plus `tmp=/tmp`;
- 454/2,284 rows: `tmp=/tmp`;
- 21/2,284 rows: `[]`.

No row was defaulted to `[]`. The zero unknown count is meaningful because all
2,284/2,284 rows were matched to immutable producer/run identities. A future
unmatched row is `UNKNOWN-RELAXATION`, never assumed strict.

## REFUSED dimension: this is not falsifiability-backed

**This result is explicitly NOT yet falsifiability-backed.** None of these
historical rows carries a qualifying `--verify-strict` verdict with both
`bitwise_parity=true` and nonzero `compared_log_messages`.

The falsifiability dimension has **0 qualifying successes / 0 qualifying
trials**. Its status is **REFUSED / NO RESULT**, never green. Equivalently,
2,284/2,284 rows are unknown on that dimension. Reporting `0/0` as 100%, as a
pass, or as evidence of strict determinism would manufacture a result from an
empty denominator.

The pending `--verify-strict` corpus re-run supersedes this interim
reclassification as the real strictness claim. Its green predicate is
`bitwise_parity == true` **and** both compared-message counts are nonzero.

## Publication boundary

The tier data, old field counts, and blank-evidence refusal are on main at the
landed snapshot above. At publication time, the relaxation schema/consumer and
historical backfill are still open draft PRs #79 and #81; they are not
ancestors of main. Therefore the relaxation-qualified column above is a
**staged reclassification**, not a live main-branch scorecard total. This
distinction is deliberate: unlanded evidence does not alter a main-branch cell.

Main scorecard blob IDs, in table order, are:

- canonical: `1ef205fa71d1180aafb4720afd81d5c69aff23e7`;
- full corpus: `d25e784e28b544240203fc02af522793297eb9cf`;
- Reverie: `0f46d77a991cc3c458f684551ccc1b09dfa6e24a`;
- e9patch: `0f94dff27fa394734d90a04fae12bcb519ccd031`.

The matching staged PR #81 blob IDs are `d69b0bf5120d6d18c0d84f664dd830ab85382ab4`,
`feeae2da1cc7a965f00cfafed40bc835226349ae`,
`d413e35e34d0cb5fc22567a42cbfb2ac7409c1cb`, and
`e06d7dc5a6a0f40cf0c97354a9c09d093e0b6ef2`.

## Reproduction

```bash
with-proxy git fetch origin main \
  refs/pull/79/head:refs/remotes/origin/pr79 \
  refs/pull/81/head:refs/remotes/origin/pr81
git rev-parse origin/main origin/pr79 origin/pr81
python3 compat-envelope/check-scorecard-tier.py --root compat-envelope
```

For each file, join main and PR #81 rows by
`(run_id,bucket,test_id,test_mode,backend)`. Count the old column where main
has `deterministic=1`; count the staged column where PR #81 has
`deterministic=1` and parsed `relaxation_set == []`; require a nonblank main
`parity_comparator` or `compared_log_messages` for the evidence-backed column;
count the combined column only when that row also has a qualifying main
`comparison_tier`. The join was total and order-identical for
**2,284/2,284 rows**.

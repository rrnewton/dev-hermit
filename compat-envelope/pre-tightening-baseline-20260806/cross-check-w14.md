# Independent cross-check of this baseline (hermit-w14)

**This is not a second baseline.** `README.md` in this directory is the
baseline. This file is an *independent replication* of it, run concurrently by a
different agent on the same host, and it exists to answer one question the
baseline cannot answer about itself:

> **How much of a before/after delta is measurement noise?**

Answer, measured: **±1 cell out of 205 per backend.** A tightening effect
smaller than that is not distinguishable from re-running the same sweep.

## The two sweeps

| | landed baseline | this cross-check |
| --- | --- | --- |
| hermit | `4c70658e785834737cbe1524f77330c781a6f5ea` | `1fadc03779f2a246a9b5af5d4a93533511c837df` |
| reverie | `dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6` | same |
| swept | 2026-08-07T02:17Z–03:47Z | 2026-08-07T02:18Z–04:04Z |
| storm width | 16 throughout | **16** (ptrace, dbi) then **4** (sabre, e9patch, liteinst) |
| rows | 1025 | 1025 |
| backends | ptrace, dbi, sabre, e9patch, liteinst | same |
| corpus | 205 effective of 235 advertised | same |

The two hermit SHAs are **one commit apart** — `4c70658e` is the parent of
`1fadc037` (`backend-parity: emit the observed value in the two fixtures that
stayed blind`). Both agents fetched `origin/main` within a minute of each other
around 01:59Z and main moved between the fetches. So this is a genuine
independent replication across a one-commit gap *and* a concurrency-width
change, not a rerun of an identical configuration.

## Determinism agreement (`--strict --verify` exit 0, stripped comparator)

| backend | landed `4c70658e` | cross-check `1fadc037` | delta | of |
| --- | ---: | ---: | ---: | ---: |
| ptrace | 182 | 183 | **+1** | 205 |
| dbi | 157 | 158 | **+1** | 205 |
| e9patch | 182 | 181 | **−1** | 205 |
| sabre | 152 | 152 | 0 | 205 |
| liteinst | 121 | 121 | 0 | 205 |

Three backends move by exactly one cell, two are identical. The moves are not
in a consistent direction (+1, +1, −1), which is the signature of per-cell flake
— overwhelmingly timeout flake, since a `--verify` timeout is recorded as a
determinism failure and this host is shared and contended.

**How to use this when the after-state lands:** treat a per-backend delta of
|Δ| ≤ 1 as noise. Only a larger, consistently-signed movement is evidence that
the contract change did something. Do not headline a single-cell delta.

## What this cross-check adds beyond corroboration

`cross-check-w14-results.csv` is a timing-free projection of the raw rows that
carries **per-row** fields the baseline's `scorecard.csv` records only at the
document level:

| column | why it is per-row |
| --- | --- |
| `determinism_contract` | `stripped-verify-l2` on every row |
| `parity_contract` | `stdout-sha256` on every row |
| `observed_tier` | `TIER-1-AT-BEST` / `TIER-0-FAIL` / `NO-RESULT-UNMEASURED` / `NO-RESULT-NOT-RUN` |
| `bitwise_axis` | `CONTRACT-UNAVAILABLE` on every row |

Tier vocabulary is hermit PR #1778's: TIER-1 exit+stdout, TIER-2 +stderr,
TIER-3 +unstripped INFO. stdout-sha256 cannot establish better than TIER-1, so a
green row is recorded as an **upper bound on the claim**, never as an achieved
tier. `bitwise_axis` is `CONTRACT-UNAVAILABLE` rather than `0` because #1595
wired `BitwiseInfoV1` into same-backend paths only and the cross-backend
comparator (`tests/backend-parity/parity_mutation.py`, PR #1778) is out-of-tree
— verified absent at `1fadc037`. Nothing was measured and failed; there was
nothing to measure with.

Row key, matching what `render-scorecard.rs` keys on:
**`(bucket, test_id, test_mode, backend)`**. The file is sorted by
`(backend, bucket, test_id, test_mode)` so it is byte-stable regardless of the
order cells finished in.

## Reproducing

```bash
./cross-check-w14-derive.py --raw cross-check-w14-raw.csv \
                            --out cross-check-w14-results.csv --check
# => REPRODUCIBLE: re-derived cross-check-w14-results.csv is byte-identical (1025 raw rows)
```

The deriver refuses a zero-row input with exit 3, because `ok` over nothing
executed is the same ambiguous zero this directory exists to eliminate.

## Two operational findings from this run

**1. A sweep staged under `/tmp` reports a total, silent, uniform red.** My
first attempt returned 0/205 on *every* cell including the ptrace reference.
Cause was not the product:

```
Error: Program /tmp/.../guest is under host /tmp, but Hermit replaces guest
/tmp with an isolated directory.
```

The control that isolates it: the same binary runs
`--strict --verify -- /bin/echo hello` to `:: Success: deterministic` rc=0.
Anyone repeating this must stage the checkout outside `/tmp`. Worth noting that
the empty-denominator guard caught it and refused to render — *"205 ptrace rows
but none passing in any mode … the reference backend itself failed here"* —
rather than publishing a confident `TOTAL 0`.

**2. The sweep leaks hung processes, and `timeout` cannot stop them.** Every
cell that exceeds its bound leaves a hermit process that ignores SIGTERM and is
reparented to init. 17 accumulated during the sabre sweep alone
(`c-programs_clone`, `bin-c_robust-futex-test`,
`c-programs_fp-reduction-nondeterminism`, `c-programs_sigtimedwait-no-timeout`,
`c-programs_vforkexec`, `c-programs_writev-determinism`,
`determinism-stress-c_mmap-fork-shared`), several past 1000 s against a 120 s
bound. The `have_backend kvm` probe leaks the same way and blocked backend
detection for ~17 minutes past its nominal 30 s bound. Reap them by exact PID
after verifying the argv is your own worktree — never by pattern. At width 16
the leak rate is roughly four times what it is at width 4, which is an argument
for the lower width independent of CPU contention.

## Files

| file | role |
| --- | --- |
| `cross-check-w14-raw.csv` | 1025 rows, collector's 19-column schema, incl. timing. Evidence. |
| `cross-check-w14-results.csv` | timing-free per-cell projection with the tier/contract columns. Reproducible. |
| `cross-check-w14-derive.py` | raw → results; `--check` re-derives and diffs. |

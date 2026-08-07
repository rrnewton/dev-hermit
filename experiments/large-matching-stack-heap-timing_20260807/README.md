# Large matching stack+heap timing

## Outcome

**No tiering needed.** One qualifying end-to-end canonical comparison of a
published LARGE corpus guest took 2.90 wall-seconds and 3.13 CPU-seconds. A
linear 346-cell extrapolation is 1,003.4 wall-seconds (16.72 minutes) and
1,082.98 CPU-seconds (18.05 CPU-minutes).

## Provenance

The run completed at 2026-08-07T09:41:13Z on short host `devbig014`, Linux
6.18.39-0_fbk0_hardened_0_ga43d5727b443 x86_64. Hermit was a clean release
build at `195614a4c208` with Reverie Cargo pin `038e993926e4`; the feature
commit changes only the E2E shell harness, so product source is identical to
Hermit `723d19ad5d10`. The current Hermit-main drift at measurement time was
only test inventory and fixtures. `metadata.json` records every full SHA and
input digest.

## Methods

The guest was `c-programs/prodcons-determinism`, built from the manifest with
GCC 11.5.0. Its published corpus duration is 10,925 ms, above the 5,000 ms
LARGE threshold. It exercises a pthread producer/consumer workload and produces
thousands of scheduler-checkpoint memory observations, making it materially
larger than the earlier one-guest micro-sample.

The exact timed invocation is in `command.txt`. Hermit executed the guest twice
under ptrace with `--strict --verify --verify-strict --detlog-stack
--detlog-heap`, deterministic minimal environment, disabled PMU timeslicing,
and a 180-second outer timeout. GNU time 1.9 measured the complete Hermit
process. There was no warmup and one measured repetition because the task asks
for one decisive LARGE matching cell. This is an absolute-cost measurement;
there is no native baseline or cross-host ranking.

Execution order was: one sandboxed setup attempt, the qualifying unsandboxed
timed attempt, then two untimed supporting captures with the identical guest
and execution flags. The setup attempt was denied at the pre-guest
PTRACE_TRACEME capability probe. It remains row 1 with `qualifying=0` and no
timing; it is not summarized as a fast negative.

For the dimension gate, both supporting logs contained 19,907 INFO and 18,340
DETLOG records. The production `compat-envelope/strict_verdict.py` at blob
`52ece2e8c833f6881dc9d17cef335f85f3e63ce5` compared the two captures. Stack
content and address each passed at 4,584|4,584 with zero differences; heap
content and address each passed at 4,547|4,547 with zero differences. Thus the
aggregate match did not hide an empty memory dimension.

Raw logs, the product verdict, GNU-time receipt, and setup failure remain in
the ignored local directory
`worktrees/w26/hermit/ignored/large-matching-stack-heap-20260807/`; binaries and
the 6.9 MiB raw capture are intentionally not committed.

## Evaluation

The benchmark answers one question: does a LARGE guest that reaches the end of
the full stdout+canonical-INFO+stack+heap path cost enough to require a cheaper
spot-check tier? Correctness requires exit zero, product verdict `matched`,
`verified=true`, `bitwise_parity=true`, equal nonzero INFO counts, and separate
nonzero matching stack and heap streams. A diverging comparison would be only a
lower bound and was not eligible.

## Results

The denominator is 1 qualifying trial from 2 launch attempts; the other attempt
is a pre-guest setup failure and contributes zero qualifying trials.

| Result | Value |
| --- | ---: |
| Product verdict | matched |
| Canonical INFO messages | 19,907|19,907 |
| Stack records, differences | 4,584|4,584, 0 |
| Heap records, differences | 4,547|4,547, 0 |
| Wall time | 2.90 s |
| User + system CPU | 2.25 + 0.88 = 3.13 s |
| Maximum RSS | 22,784 KiB |
| 346-cell linear wall extrapolation | 16.72 min |
| 346-cell linear CPU extrapolation | 18.05 CPU-min |

The measured full-comparison total is small enough that the requested decision
is **no tiering needed**. This retracts neither the matching verdict nor the
dimension evidence when interpreting cost.

Limitations: n=1, one ptrace guest, one host, no randomized order, and no
uncertainty interval. The 346-cell value is a transparent linear extrapolation,
not a measured distribution, worst-case bound, or candidate-backend performance
claim.

# Current ratchet figures with measurement coverage

**Date:** 2026-08-07
**Task:** `executed-count-applies-to-the-ratchet-artifacts-too`

This is the denominator-bound republish of the three live readings named by the
task. The two prefix figures count different record types and different guest
populations. They are therefore separate ratchets, not contradictory estimates
of one quantity.

| ratchet | published figure | actually measured / declared total | repetitions and qualification |
|---|---:|---:|---|
| KVM COMMIT-turn prefix, named nine-guest population | minimum `Y=2`; `/bin/true` is `2/5` | **8/9 guest × KVM-backend pairs** | `n=1` per pair; pipeline exited `rc=1`, so it is zero qualifying trials and no depth |
| KVM completed-syscall prefix, heapwalk | record `20 -> >139` (records 0..139 matched) | **1/1 guest × KVM-backend pair** | `n=3`; **3/3** ptrace/KVM run pairs and **140/140** observed records matched |
| backends with no ptracer in the syscall path | **2/6 FREE**, 4/6 PRESENT | **6/6 backend litmus cells** | KVM had 1 qualifying retained-output trial; 3 console-capture attempts with no verdict are zero qualifying trials, not negatives |

## Why `2/5` and `>139` do not conflict

`2/5` is the `/bin/true` row of the **COMMIT-turn** prefix metric. The same
metric yielded `Y=2` for all eight qualifying guests, while each guest's golden
length `Z` varied. Coverage is 8/9 because the declared population included the
pipeline pair, whose guest/backend run failed and therefore produced no depth.
The complete per-guest table and controls are in
`experiments/prefix-parity-depth-multiguest_20260807/`.

`record 20 -> >139` is a **completed-syscall sequence** lower bound for the
heapwalk guest at Reverie PR #402 head
`867fce5b6cd6fb936cb6aa72daba837ec5e807c6`. It covers one named guest/backend
pair, repeated three times. `>139` means no divergence was observed within
records 0 through 139; it is not a claim of infinite or multi-guest parity.

## Ptracer republish

Definition: `strace-attach-litmus/v1`. Linux permits only one tracer per
tracee, so successful end-to-end external `strace` attachment proves that the
backend did not itself occupy the ptracer slot.

| backend | result | evidence |
|---|---|---|
| ptrace | PRESENT | `PTRACE_SEIZE` refused with `EPERM(1)`; `TracerPid` named Hermit |
| DBI | FREE | external attach succeeded; errno none; `TracerPid=0` |
| SaBRe | PRESENT | `PTRACE_TRACEME=-1 EPERM(1)`; 1/1 establishing calls refused |
| LiteInst | PRESENT | `PTRACE_SEIZE` refused with `EPERM(1)`; `TracerPid` named Hermit |
| e9patch-preprocessed | PRESENT | `PTRACE_SEIZE` refused with `EPERM(1)`; `TracerPid` named Hermit |
| KVM | FREE | Hermit `590fcc9eeb0339c5cf23f72b84394a63333e88ff`, Reverie `6144323c5dab8b521278fce206f8774360c2b05f`: 546 strace lines, zero establishing calls, `HERMIT_RC=0`, `VERDICT=ATTACHED`, errno none |

The rows retain their original evidence revisions. This republish does not
pretend all six backends were rerun at one revision.

## Zero-measurement behavior

Every non-null value in `ci-hub/ratchet/metrics.json` must carry
`coverage: {measured, total, unit}`. A published value with `measured=0` is
refused. The honest zero-result form is `value:null` plus
`unmeasured_reason`, which renders UNMEASURED and cannot reuse a prior value as
unchanged. The multi-guest sweep likewise prints `COVERAGE measured=0 ...`,
prints `RATCHET UNMEASURED`, and exits nonzero if no pair qualifies.

## Coverage audit

Before the coverage guard landed, **4/4 published machine-readable figures**
lacked a measured count. The guard corrected those four, but the three live
readings above were still absent from the guarded artifact. Thus **3/3
task-named current figures lacked one durable, versioned, coverage-bound
republish**. This document and the corresponding metrics make all three
populations explicit.

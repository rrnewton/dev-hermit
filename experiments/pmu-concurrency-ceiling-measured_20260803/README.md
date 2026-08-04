# Measured PMU concurrency ceiling for hermit `--strict --verify`

**Date:** 2026-08-03 · **Host:** devbig014 (AMD EPYC 9D85, 316 threads, `perf_event_paranoid=1`, `nmi_watchdog=1`) · **Hermit:** `main@9e85f02f` release binary · **Agent:** hermit-250

## Question

The verify tier was pinned to **serial-1** by three redundant mechanisms (a
`pmu-serial` runner label, a `flock /tmp/hermit-privileged-pmu.lock`, and
`resource_caps {pmu:1}` in `hermit/ci/dag/privileged.json`). The "1" was never
derived from any measurement. **How many concurrent PMU-consuming hermit runs
does this hardware actually support before `perf_event_open` fails?**

## Answer

**The PMU-imposed concurrency ceiling is not 1, and not any small hardware
number. The "1" is fictional.** Even **32** concurrent `hermit run --strict
--verify` forced onto a **single core** (5 usable PMCs) all pass with zero
failures and zero PMU panics.

## Method + results

### 1. Direct per-core pinned-PMC capacity (`pmc_probe.c`)

Open N pinned per-task (`pid=0, cpu=-1`) `PERF_COUNT_HW_BRANCH_INSTRUCTIONS`
events, `taskset` self to one core, run a loop, read
`TOTAL_TIME_ENABLED`/`TOTAL_TIME_RUNNING`, count `enabled==running` (fully
resident).

```
N=8 -> events 0..4 RESIDENT, events 5..7 SHORT READ (ERROR state)
RESULT: 5 of 8 pinned per-task PMCs fully resident on one core
```

**5 usable pinned PMCs/core** (6 generic HW PMCs − 1 NMI watchdog). The 6th+
goes to the ERROR state that yields a short read — the exact condition reverie's
panic guards fire on. This **resolves the 2-vs-5 discrepancy** in the prior
record: the "2" was a `perf stat` multiplexing-scaling artifact; the direct
pinned probe (what reverie actually does) is authoritative at **5**.

### 2. Cross-process K-sweep (all pinned to ONE core, max contention)

`taskset -c 3 hermit run --strict --verify -- /bin/true`, K concurrent:

| K  | verified | failed | `pinned perf event descheduled` panics |
|----|----------|--------|----------------------------------------|
| 1  | 1/1      | 0      | 0 (baseline: RCB/PMU confirmed active) |
| 8  | 8/8      | 0      | 0                                      |
| 16 | 16/16    | 0      | 0                                      |
| 32 | 32/32    | 0      | 0                                      |

## Interpretation

reverie creates **2 pinned per-task counters per guest thread** (`timer` +
`clock`, both `cpu=-1`; `reverie/reverie-ptrace/src/timer.rs:597-616`). Because
the events are **per-task**, the kernel saves/restores them on context switch —
only the currently-running task's 2 counters need be resident. Detcore
serializes each run's guest threads onto ~one core, so concurrent *runs* never
co-reside more than 2 counters on a core. The 5-PMC/core budget only binds if a
**single process** runs >2 RCB-counted guest threads *simultaneously* on one
core (a future parallel in-guest backend) — which Detcore prevents today.

If the budget IS ever exceeded, hermit **hard-fails** (`panic!("pinned perf
event descheduled!")`, `reverie-ptrace/src/perf.rs:350-354` slow read /
`440-446` fast read; `set_pinned(1)` at `perf.rs:210`). It **never** silently
multiplexes / returns scaled counts. Visible and retryable, never silent
nondeterminism.

## Consequence

The serial-1 verify tier guards a PMU-counter limit that **does not bind**. The
depth-25 lane backlog is self-imposed serialization, not hardware. Remediation
is to retire the counter-exhaustion guards (flock + `pmu:1`), keeping any
host-global bound as a single authority in `safe-ci-dag-runner`
(`scheduler.rs:127`) if co-tenancy noise still warrants one. Caveat: verify
per-job whether a `pmu-serial` label exists for **timing-determinism** of
skid-sensitive demos (separate rationale) before removing it there.

## Reproduce

```sh
cc -O2 -o /tmp/pmc_probe pmc_probe.c && /tmp/pmc_probe 8
for K in 8 16 32; do
  for i in $(seq 1 $K); do taskset -c 3 hermit run --strict --verify -- /bin/true & done; wait
done
```

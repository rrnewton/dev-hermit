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

---

## Independent re-verification, 2026-08-05

Re-run against the **current** reverie pin `025d37800d347c32711038bd0a3889e8e4774c2b`,
same host (devbig014, AMD EPYC 9D85 158-Core, nproc=316, `perf_event_paranoid=1`,
`nmi_watchdog=1`). Purpose: the record's code anchors had drifted, and a claim that rests on
line numbers decays silently.

### Per-core ceiling reproduced, with the boundary pinned

`taskset -c 7 /tmp/pmc_probe N`:

| N | fully resident | boundary |
|---|---|---|
| 4 | 4 of 4 | — |
| 5 | 5 of 5 | last N with full residency |
| 6 | 5 of 6 | **first failure** |
| 7 | 5 of 7 | — |
| 8 | 5 of 8 | events 5,6,7 → SHORT READ |

**5 usable pinned PMCs/core, confirmed independently.** The "2" in the earlier record stays
refuted (it was a `perf stat` multiplexing-scaling artifact, not a pinned-per-task count).

This run also closes the causal link that the original write-up left implicit: the events past
the ceiling return a **SHORT READ**, which is exactly the ERROR-state condition
`perf.rs` turns into `panic!("pinned perf event descheduled!")`. Exhaustion → error state →
short read → panic is now observed end-to-end at the kernel boundary, not just argued from
`pinned=1`.

### Anchors re-confirmed BY CONTENT (line numbers had drifted)

Cite these by content; the line numbers in the sections above are stale.

| fact | current location |
|---|---|
| `attr.set_pinned(1)` — "error state if we are descheduled from the PMU" | `perf.rs:210` |
| slow-read guard, EOF → `panic!("pinned perf event descheduled!")` | `perf.rs:353` (was 350-354) |
| fast-read guard, `running != enabled` → `panic!` | `perf.rs:492` (was 440-446) |
| **rdpmc guard, `running != enabled` → `panic!`** | `perf.rs:575` (**new since this artifact**) |

**There are now THREE read paths, not two.** The `rdpmc` fast path was added after this
experiment was written, and it carries the same abort-don't-scale guard. That is the load-bearing
observation: the fail-fast invariant survived the addition of a new read path. It is also the
thing most likely to regress silently — a future fourth read path that scales by
`enabled/running` instead of aborting would reintroduce case (b), the catastrophic-and-invisible
one, with no test to catch it.

### Remediation confirmed landed

All three serial-1 mechanisms are gone from `hermit` main; only explanatory comments remain.

| mechanism | state |
|---|---|
| `resource_caps {pmu:1}` in `ci/dag/privileged.json` | gone — now `{"kvm": 1}`; zero nodes tagged `pmu` |
| `flock /tmp/hermit-privileged-pmu.lock` | no live use (prose in `ci/dag/README.md` only) |
| `pmu-serial` runner label | no live use (comments in 4 workflows explaining the retirement) |

### The one place this ceiling DOES become load-bearing

Under Detcore the 5-PMC budget never binds, because guest threads are serialized onto ~one core
and per-task counters are context-switched. That safety argument **does not transfer** to a
parallel in-guest backend, whose whole purpose is co-scheduling many RCB-counted guests across
cores simultaneously. There the arithmetic is direct:

> 5 usable PMCs/core ÷ 2 pinned counters per RCB-counted guest thread
> = **2 concurrent RCB-counted guest threads per core**; the 3rd needs 6 and panics.

Measured, not estimated: N=6 is the first failing point above. For `goal-hermit-v2`'s parallel
backend this is admission control for **correctness-via-fail-fast**, not CI throughput — and it
is the number to design against. Re-measure per host: a host with a different generic-PMC count
or without the NMI watchdog holding one will have a different budget.

# Concurrent `hermit run --strict` scaling — does the validate strategy invert?

**Task:** CURVE 3 of `headline-inner-step-scaling-curves-cargo-and-strict-compat`.
**Question (owner):** *Does concurrent `hermit run --strict` scale past 150 cores? If it
scales, the validate strategy inverts — from "serialize to avoid contention" to "run the
corpus wide."*

**Agent:** hermit-ghdag (opus-4.8). **Date:** 2026-08-04.
**Hermit SHA** `a6201fc65d29c3a1a88cc7af4b117b68e9950284` ·
**Reverie SHA** `591154213a98f35cdd6b14274c42113ccd204266`.
**Host:** devbig014, AMD EPYC 9D85 class, **158 physical × 2 SMT = 316 threads**, kernel
6.18.39-fbk.

> **Box state caveat — read first.** The box was **NOT quiet** during measurement
> (hermit-231b's curves 1+2 — cargo build / strict_compat — plus codex/rustc were live).
> Every data row is labeled `contended`. The **high-N knee (N ≥ 128) was NOT measured** and
> still requires a quiet box. What is reported below is (a) two premise-correcting facts that
> are load-insensitive and solid, and (b) a *lower bound* on the scaling knee plus a stability
> ceiling, both of which a quiet run can only push higher, not erase.

---

## Headline

Concurrent hermit **does** scale in throughput at first (**~19× aggregate throughput from
N=1 to N=32**), so the "run the corpus wide" instinct is directionally right — **but** the
naive form of that inversion is wrong for two concrete, measured reasons:

1. **A `hermit run --strict` instance is not a one-core workload.** Pinning each instance to
   its own single core (the originally-planned sweep design) makes each instance **14× slower**
   — that curve measures a confinement artifact, not real per-instance cost. "Wide" must mean
   *~one instance per several cores with the OS scheduler free to place its threads*, not *one
   instance per core*.
2. **There is a clone-path stability ceiling well before core saturation.** Under high
   concurrency, instances crash — `reverie_process::clone::clone_with_stack` non-unwinding
   panic → SIGSEGV — at a low but nonzero rate (**~3% at N≈96**). This is a hard crash, not a
   clean backpressure error, and it must be fixed/characterized before any wide fan-out is
   trusted for validation.

**Decision this creates:** the "invert to wide" strategy is viable *only* if (a) validate does
**not** pin instances to single cores (it currently would over-subscribe wrongly if it did),
and (b) the reverie clone crash is fixed or the fan-out width is held below its onset. Absent
both, wide fan-out trades serialization for a crash-and-retry tax.

---

## Finding 1 — the single-core-confinement inversion (load-insensitive, solid)

Same guest (`spin_guest_30m`, 30M iters), same `--strict`, only the CPU placement differs:

| placement | wall | user | sys | cpu-time | vs free |
|---|---|---|---|---|---|
| **free** (no taskset, whole machine) | 0.20s | 0.05 | 0.15 | **0.20s** | 1× |
| `taskset -c <one core>` | 2.76s | 0.06 | 2.71 | **2.77s** | **14× slower** |
| `--pin-threads`, free | 0.19s | 0.04 | 0.15 | 0.19s | 1× |
| `--pin-threads` + `taskset -c <one core>` | 2.74s | 0.06 | 2.67 | 2.73s | 14× slower |

The variable is **single-core confinement**, not `--pin-threads`. Confinement inflates
**system** CPU ~18× (0.15s → 2.71s) for identical guest work. A single instance is a **4-thread
tree** (hermit main + a 2-thread supervisor + guest); the async detcore/reverie supervisor
needs scheduler headroom across a few cores. Forced onto one core, tracer/supervisor/guest
storm the run queue → the cost is context-switch system time, not compute.

Corollary observed directly (`verify the running thing`): with each instance `taskset`-pinned
to a distinct core, **all guest processes still landed on host core 0** (`psr=0`), because
detcore normalizes guest affinity to virtual CPU 0 (`detcore/.../threads.rs:1001`
"affinity remains virtual CPU 0") and the confined tree collapses onto one host core. `taskset`
on the parent does **not** give you one-instance-per-core parallelism.

**Consequence for the original CURVE-3 plan:** the "pin instance i to core i, sweep N" design is
invalid — it measures 14× confinement overhead and a fake serialization (makespan = N×T1,
flat throughput). Those rows are retained in `results.csv` under `confined_artifact_sweep` and
explicitly labeled ARTIFACT.

## Finding 2 — corrected scaling curve (free instances; box contended)

N free instances (no taskset, OS schedules them), `spin_guest_300m` (300M iters, ~2s solo).
Aggregate throughput = N × 300M / makespan:

| N | makespan | agg throughput (Giter/s) | failures |
|---|---|---|---|
| 1 | 2.63s | 0.114 | 0 |
| 2 | 2.75s | 0.218 | 0 |
| 4 | 4.22s | 0.284 | 0 |
| 8 | 3.14s | 0.765 | 0 |
| 16 | 3.63s | 1.321 | 0 |
| 32 | 4.47s | **2.148** | 1 |
| 64 | 10.50s | 1.828 | 7 |

Throughput climbs monotonically to **2.148 Giter/s at N=32 (~19× over N=1)**, then **regresses
at N=64** as failures spike. Because the box was contended, the N=64 regression point is a
**lower bound** on the true knee — a quiet box moves the knee toward the expected topology
limit (~158 physical / 316 threads), it does not move it lower. The precise knee is the one
remaining measurement and needs a quiet box.

## Finding 3 — stability ceiling: reverie clone crash under concurrency

At N≈96 free instances, **3/96 (~3%)** aborted with:

```
reverie_process::clone::clone_with_stack::callback
__clone
thread caused non-unwinding panic. aborting.
Error: Sandbox container exited unexpectedly  (Signaled(SIGSEGV, true))
```

This is a **concurrency-induced crash in reverie's process-clone path**, not a clean
resource-limit error. It first appears at N=32 (1 crash) and grows with N. It is a distinct
failure mode from throughput saturation and is the more dangerous one for a wide-fan-out
validate strategy: a corpus run that fans out past its onset will silently lose cells to
SIGSEGV unless it retries. **Follow-up worth filing:** characterize/repro the clone crash on a
quiet box (is it a stack-allocation race, an mmap/thread limit, or a genuine data race in
`clone_with_stack`?) — it may be a real reverie bug independent of this benchmark.

---

## What is NOT established here
- The high-N knee (N = 128, 150, 158, 200, 256, 316, 400) on a **quiet** box. Held pending
  coordination with hermit-231b (curves 1+2). Run the free-instance sweep (no taskset) at
  those N on a quiet box; expect the knee near 158 (physical) with a softer SMT tail to 316.
- Per-N RSS-sum. Not a concern for this guest: measured maxRSS ≈ **10.6 MB/instance**, so even
  316-way ≈ 3.4 GB — memory is not a constraint for compute guests, and the memory.peak
  cap-inflation concern does not bind here (state it explicitly if a real corpus guest is used).
- Whether a **compute-heavy** guest (vs this branch-dense one) scales better. This guest is
  system-time-dominated under `--strict` (RCB preemption traps on retired conditional
  branches), which probes hermit's ptrace/PMU supervision path — the right worst case, but a
  second arithmetic-heavy variant would bracket best case.

## Reproduction
See `metadata.json`. Guest source in `scratch/curve3-concurrent-hermit/spin_guest.c`;
`cc -O2 -DITERS=<n>ULL -o spin_guest_<n> spin_guest.c`. Free sweep = N background
`hermit run --strict -- spin_guest_300m` with **no taskset**, makespan = first-launch to
last-exit, count nonzero exits. Confinement contrast = same command free vs under
`taskset -c <core>`.

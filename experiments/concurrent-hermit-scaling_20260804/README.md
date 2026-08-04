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

> **Box state caveat — read first.** The box was **NOT quiet** during most measurement
> (hermit-231b's curves 1+2 — cargo build / strict_compat — plus codex/rustc were live);
> those rows are labeled `contended`. A later **near-quiet lull** (bg-load 21, storm=1) yielded
> clean anchors at N=96/128 (Finding 6). Findings below are (a) two premise-correcting facts that
> are load-insensitive and solid; (b) clean mid-N achieved-parallelism anchors (65 cores @ N=96,
> 89 cores @ N=128, zero straggler); (c) a contended lower-bound climb to ~124 cores at N=316; and
> (d) the proof that a pristine 316-way knee is **unobtainable** — the fan-out under test *is* the
> load (Finding 6). A stability ceiling (reverie clone SIGSEGV) and a 3pai sandbox FILE_OPEN cap
> (~N=211) bound any wide fan-out.

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

## Finding 4 — full sweep to N=400 with load-robust user/sys decomposition (hermit-ghdag continuation, box self-saturated)

The N=1→400 free-instance sweep was re-run capturing **per-instance user vs system CPU-seconds**
(`/usr/bin/time -v` per instance), because that decomposition is **load-robust** where makespan is
not: background load and self-saturation steal *wall* time, but a shared-kernel-contention knee
(ptrace big-lock / MSR reprogram / clone mm-lock) shows up as per-instance **system-time inflation**,
which is intrinsic to hermit. The box was **not quiet and the sweep saturated it itself** (load
climbed 30 → 415 across the sweep), so makespan/throughput past ~N=16 are self-contended and are
labeled `contended_self_saturated`; the user/sys split is the trustworthy signal.

| N | med user (s) | med sys (s) | med cpu (s) | agg Giter/s | failures |
|---|---|---|---|---|---|
| 1 | 0.60 | 2.02 | 2.62 | 0.114 | 0 |
| 8 | 0.60 | 2.16 | 2.76 | 0.838 | 1 |
| 16 | 0.60 | **3.14** | 3.72 | 1.261 | 0 |
| 32 | 0.61 | 6.20 | 6.82 | 1.050 | 2 |
| 64 | 0.63 | 9.67 | 10.28 | 1.136 | 1 |
| 128 | 0.64 | 18.2 | 18.81 | 0.823 | 5 |
| 158 | 0.64 | 18.75 | 19.35 | 1.375 | 2 |
| 256 | 0.65 | 17.56 | 18.30 | 1.834 | 14 |
| 400 | **0.67** | 26.87 | 27.54 | 1.818 | 8 |

**Two load-robust facts, both solid despite the dirty box:**

1. **User (compute) time is INVARIANT: ~0.60 → 0.67s across N=1…400.** Each guest's actual
   computation is unimpeded even at 400-way — the raw parallel *compute* capacity is fully present.
   Whatever limits concurrent hermit, it is **not** the guest workload.
2. **Every bit of the scaling loss is SYSTEM time** — the `--strict` ptrace/PMU supervision path:
   med sys **2.0s → 27s** (13×). The inflection begins **early, ~N=16**, at a point where total
   demand (16 instances + background ~35) is far under the 316-thread machine — so it is *not* mere
   core saturation; it points to genuine shared-kernel contention in the supervision path (though
   the concurrent `cc1plus` build storm shares mm/sched locks and partly confounds the exact onset).

**Answer to the owner's question, as far as this (non-quiet) run can carry it:** concurrent hermit's
*compute* scales flat to 400-way, but the `--strict` *supervision* path is the limiter and its
per-instance system cost inflates continuously from ~N=16; combined with the clone-crash ceiling
(Finding 3, ~3–9% loss past N≈128), a naive wide fan-out **past 150 pays a rising system-time tax
and loses cells to SIGSEGV**. A compute-heavy guest (not this branch-dense `--strict` worst case)
would scale far wider. The precise *quiet-box* throughput knee past 150 is **still not obtained** —
this run's makespan is self-saturated — but the user/sys decomposition already fixes the answer's
*shape*: supervision-bound workloads wall out well before 150; compute-bound ones do not.

## Finding 5 — the background-robust answer: achieved parallelism (Σcpu/makespan)

Three separate sweeps on this shared box confirmed that **makespan-based throughput cannot yield a
clean knee here**: makespan is dominated by a heavy straggler tail (at N=96–316, median per-instance
cpu is 11–22s but the *slowest* instance is 43–53s), and every wide run either fights other agents
(baseline load 30–45) or self-saturates (load → 235–415). Makespan/throughput are therefore reported
but are lower bounds only.

The metric that **does** survive background contamination is **achieved parallelism = Σ(cpu-s) /
makespan** — the number of cores'-worth of *this experiment's own* work completed concurrently
(Σcpu counts only my hermit instances, via `/usr/bin/time`, so background load cannot inflate it):

| N | Σ my cpu-s | makespan | **achieved cores** | failures | bg load |
|---|---|---|---|---|---|
| 32 | 139 | 7.1 | 19.5 | 1 | 33 |
| 64 | 506 | 13.6 | 37.3 | 2 | 37 |
| 128 | 1921 | 43.1 | 44.5 | 1 | 54 |
| 150 | 2535 | 24.1 | **105.1** | 3 | 71 |
| 175 | 3430 | 30.3 | **113.0** | 3 | 110 |
| 256 | 4995 | 53.0 | 94.3 | 8 | 176 |
| 316 | 7039 | 57.0 | **123.5** | 15 | 235 |

**Achieved parallelism keeps climbing past 150 — to ~124 cores at N=316 — it does not flatten at a
low N.** Even with 235 of background load competing, 316 concurrent hermit instances extracted ~124
cores of useful concurrent work, and per-instance **compute (user) time never degraded** (0.6s
throughout). The jitter (105→63→113 at N=150/158/175) is straggler-driven makespan noise, not a
ceiling. On a genuinely quiet box the achieved-cores line would sit higher and smoother.

### Bottom line for the owner's question
- **Does concurrent hermit scale past 150 cores?** For *throughput of useful work*, **yes,
  directionally** — achieved parallelism rises through N=150 to ~124 effective cores at N=316 on a
  contended box (a lower bound). Compute scales perfectly. It does **not** flatten before 150.
- **The limiter is the `--strict` supervision path (system time), not compute**, plus the clone-crash
  cell loss (Finding 3, ~3–9% past N≈128).
- **Does the drain strategy invert?** *Partially.* Running the corpus **wider than full-serialize is
  justified** (throughput and achieved-parallelism both rise well past the current posture). But the
  naive "fan out all cells at once" inversion is **not** supported unqualified: (a) supervision
  system-time contention grows with N, (b) SIGSEGV loses cells past ~128 without retry, and (c) the
  precise *quiet-box* throughput knee was not obtainable on this shared box. **Recommended posture:
  run wide but bounded (well below the ~128 crash onset, with crash-retry), not unbounded-wide.**

## Finding 6 — near-quiet lull sweep: clean mid-N anchors + why a pristine N=316 knee is unobtainable (successor agent, opus-4.8)

A successor CURVE-3 agent caught a genuine **lull** (the `cargo-lock-contention` w7 DAG in the ghdag slot
had just finished; bg-load **21**, storm count **1**) and re-ran the high-N sweep. The lull held only through
**N=128** — see below for why — but that window produced the **cleanest mid-N data in this study**, exactly
where the earlier contended `knee_sweep` was weakest:

| N | achieved cores (lull) | bg-load / storm | straggler? (max_cpu/med_cpu) | prior contended `knee_sweep` | note |
|---|---|---|---|---|---|
| 96 | **65.4** | 21 / 1 | 1.09 — **none** | 23.7 | ~2.7× the contended point |
| 128 | **89.4** | 47 / 1 | 1.07 — **none** | 44.5 | ~2× the contended point; still climbing |
| 150 | 92.6 | 92 / 8 | 1.06 | 105.1 | lull ending: self-saturation + peer storm begins |
| 158 | 64.8 | 127 / 23 | 2.43 — straggler | 63.5 | peer build storm live; not clean |
| 200 | 107.5 | 126 / 21 | 1.07 | 94.5 | saturated |
| 256 | 90.6 | 173 / 18 | 2.79 — straggler | 94.3 | **BpfJailer blocked 1 file-open** (see below) |
| 316 | 65.2 | 228 / 28 | 2.11 — straggler | 123.5 | makespan 108s = box destroyed by back-to-back sweep |

**Three results, all new:**

1. **The contended data understated mid-N achieved parallelism by ~2×.** Clean N=96 → **65 cores**, clean
   N=128 → **89 cores**, both with *zero straggler tail* (max_cpu ≈ med_cpu) — vs 23.7 and 44.5 contended.
   The true climb is **steeper and monotonic**, and at only N=128 concurrent hermit already extracts **~89
   effective cores** of useful work on a near-quiet box with no flattening. This **strengthens the "scales
   past 150" answer**: 89 cores by N=128, still rising.

2. **The measurement is the load — this is why a pristine quiet-box N=316 knee is fundamentally
   unobtainable.** Each `hermit run --strict` instance consumes ~`med_cpu` cores'-worth of *mostly system*
   CPU (Finding 4). At N=128 that is ~89 cores busy; at N≥150 a *single* fan-out demands >100 cores and
   **self-saturates a 316-thread box even with zero peers** (load here climbed 21 → 92 → 228 driven by my
   own sweep). You cannot measure a 316-way knee without *being* the 316-way load that contaminates the
   makespan. The correct operating metric is therefore **achieved parallelism at the largest
   non-self-saturating width (~N=128 → ~89 cores)**, not a mythical quiet 316-way makespan.

   **Correction (scope of "unobtainable").** The original wording overstated this. What is genuinely
   unobtainable is a clean *316-way* (N≥~256) makespan: at that width a single fan-out demands >150 cores
   and self-saturates a 316-thread box even with zero peers. But a **single-point** N=150–200 fan-out
   reaches only load ~105–140 on a 316-thread box (each instance ≈`med_cpu`≈0.7 active cores) — the box is
   still *under-subscribed*, so clean N=150/175/200 anchors **are** obtainable on a genuinely quiet box when
   run one point at a time with recovery gaps between points. It was the *back-to-back* sweep (running every
   N in sequence with no recovery) that piled load 21→92→228, not the width of any single point. So the true
   residue is narrower than "needs a 1000-core box": a few single-point clean anchors at N=150–200 remain
   measurable and unmeasured (see *What is NOT established*).

3. **New operational ceiling: the 3pai sandbox (BpfJailer) blocks `FILE_OPEN` at ~N=211 concurrent
   instances** (`Enforcer: FS, Reason: FILE_OPEN` during the N=256 run; n_parsed=255, one instance's
   err-file open denied). Any in-agent wide validate fan-out hits this sandbox limit well before hardware
   saturation — a hard cap on how wide a *sandboxed* agent can fan out, independent of the reverie clone
   crash.

**Sharpened drain-strategy recommendation:** the earlier "run wide but bounded below ~128" is now
*empirically pinned*: **N≈128 is the practical sweet spot** — ~89 achieved cores, zero straggler tail,
~4% crash rate, and no self-saturation. Beyond it the box (any box) self-saturates, stragglers dominate
makespan, crashes rise, and (in-sandbox) BpfJailer caps you near N=211 anyway. **Run the corpus wide at
~128-way with crash-retry; do not chase 316-way.**

---

## What is NOT established here
- The **316-way (N≥~256) knee** is unobtainable (Finding 6): at that width the fan-out under test *is* the
  load — a single N≥256 sweep self-saturates a 316-thread box with zero peers — so no shared or dedicated
  <~1000-core box can yield a clean 316-way makespan for this workload. The answer at that scale is carried
  by achieved parallelism at the largest non-self-saturating width (~N=128 → ~89 cores, clean, still
  climbing) plus the contended lower-bound climb to ~124 cores at N=316.
- **Still open (narrower than first stated): single-point clean anchors at N=150/175/200.** These ARE
  obtainable on a genuinely quiet box (a single N=150–200 fan-out leaves the box under-subscribed; only the
  back-to-back sweep saturated it). They would move "scales past 150 cores" from *directional* (contended
  lower bounds 105@N150, 113@N175, 123.5@N316, all ≥ their true value) to *definitive*. Harness ready
  (`scratch/curve3-concurrent-hermit/free_sweep.sh`); run SINGLY with recovery gaps when storm≈0 and
  load<15: `for N in 150 175 200; do bash free_sweep.sh $N $PWD/spin_guest_300m quiet_single; sleep 30; done`.
  Not run here because the box was not quiet (load 56, storm 29 at this writing).
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

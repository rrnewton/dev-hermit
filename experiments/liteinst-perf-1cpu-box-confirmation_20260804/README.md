# LiteInst perf attribution — 1-CPU-box confirmation (closes the method caveat)

**Date:** 2026-08-04 · **Lane:** hermit-perf · **Task:** `liteinst-perf-attribution-fastpath` (P0)

## Why this run exists

The prior attribution
(`ai_docs/liteinst-perf-attribution-fastpath-is-leader_20260804.md`) established
that the in-guest LiteInst patch fastpath is the **fastest** backend, not "14.5x
broken" — but it carried one explicit, non-negotiable method caveat: the mandated
**1-CPU sequentializing box was NOT available** (blocked on
`runner-cpu-affinity-single-core-runs`), so all numbers were measured **unboxed,
under high background load**. The owner's concern: Hermit sequentializes threads,
so a blended/unboxed number cannot separate a **broken fastpath** from a
**sequentialization cost**, and an absolute ns figure taken under load is
host-sensitive.

That box is now available via `sched_setaffinity`. This run re-measures the same
workloads **inside the 1-CPU box** and adds a **same-session unboxed contrast**,
to (a) get contamination-free absolute anchors and (b) prove whether
sequentialization is a confound.

## Box mechanism ACTIVE (surfaced per directive)

- **Mechanism: `sched_setaffinity`** (K=1), via
  `scratch/run-on-k-free-cores.py 1 -- python3 run.py ...`. The whole process
  tree (runner → native/backend guests → any supervisor) inherits the mask
  across fork+execve.
- **Why not cgroup cpuset:** `cpuset` is **NOT delegated** on this 3pai sandbox —
  the scope's `cgroup.controllers` is `io memory pids` (no cpuset). Checked live.
  `sched_setaffinity` is the only working size-K box on this host. On hosts where
  cpuset *is* delegated (CI/self-hosted) a future runner feature should prefer
  cpuset and fall back to affinity, logging which is active.
- **Core selected: 9** (least-busy free core, chosen dynamically; never a fixed
  pinned id). See the core-0 caveat below — core selection is load-bearing.

## Identity

- Host: `devbig014`, AMD EPYC 9D85, 316 logical CPUs
- Reverie: `bfea4d5aa7d662cacf21f41ff2df5b60925dff2d` (main; the landed counter2
  shootout `a9f25aa7` is an ancestor)
- Harness: `reverie/benchmarks/counter2-shootout/run.py`, **release** profile,
  seed 1, `--target-seconds 3`, `--repetitions 3`, `--warmups 1`
- Fixtures (single-threaded, direct x86-64 syscall sites in the main ELF):
  - `counter2-cpu-heavy` — one `getpid` per 65,536 iterations (~16.8k calls)
  - `counter2-syscall-mix` — one `getpid` per 4,096 iterations (~267k calls)
  - calibrated iteration count ≈ 1.10e9 for a ~3.0s native target
- Correctness gate (harness-enforced before timing): every native/backend cell
  exited 0, matched native stdout, and emitted a nonzero exact-counter2 total.

## Result 1 — INSTRUMENTATION cost (boxed K=1, parallelism removed)

Medians of 3 reps after 1 warmup. Native ≈ 2.97–2.98s (matched work both cells).

| Backend | Geomean slowdown | cpu-heavy | syscall-mix | **Paired marginal ns/syscall** |
| --- | ---: | ---: | ---: | ---: |
| **liteinst** (in-guest patch) | **1.030x** | 1.009x | 1.051x | **496** |
| ptrace | 1.576x | 1.092x | 2.273x | 14,032 |
| kvm | 1.581x | 1.095x | 2.282x | 14,113 |

On one idle dedicated core, **LiteInst's in-guest patch fastpath costs ~0.50µs
per syscall — ~28× less than ptrace (14.0µs) and ~28× less than reverie-KVM
(14.1µs)**. On a dedicated core, wall time ≈ total-tree CPU-time (everything
serializes on that one core), so these are defensible CPU-time-equivalent anchors,
not load-contaminated wall figures.

## Result 2 — PARALLELISM cost (boxed K=1 vs same-session unboxed)

| Backend | Boxed K=1 geomean | Unboxed geomean | Boxed ns/call | Unboxed ns/call |
| --- | ---: | ---: | ---: | ---: |
| liteinst | 1.030x | 1.038x | 496 | 588 |
| ptrace | 1.576x | 1.576x | 14,032 | 13,363 |
| kvm | 1.581x | 1.612x | 14,113 | 14,920 |

**Boxing to one core changes nothing material (<3% geomean, all within
run-to-run noise).** For these single-threaded syscall fixtures the
sequentialization penalty is ≈0 **by measurement**, not merely "by construction"
as the prior report could only assert. Therefore the per-syscall instrumentation
attribution above is *not* contaminated by sequentialization, and the "LiteInst
is the leader" conclusion holds under a proper apples-to-apples box.

The sequentialization axis is real but **only appears on multi-threaded guests**
(Hermit serializes threads onto its scheduler). The counter2 corpus is
single-threaded, so it cannot exhibit it; measuring it needs a multi-threaded
fixture and is a separate task, not a correction to this attribution.

## Caveat — core selection is load-bearing (a real methodological finding)

A first smoke run happened to pick **core 0** and reported ptrace syscall-mix at
**67s (22.6x)** — ~10× the core-9 figure. All-cores-unboxed and core-9-boxed both
give ~6.7s, so the 67s is **core-0 contention** (IRQ/kernel-thread noise), an
artifact, not a ptrace sequentialization tax. It was **not** reported as signal.
Lesson: the recipe's "least-busy *free* core" selection matters; a fixed/atypical
core (esp. core 0) can inflate an out-of-process backend by ~10×. The reported
run used core 9 and matches the unboxed all-core result.

## Reference frame

Owner reference latencies: systrap ~8µs, KVM ~29µs, ptrace ~40µs per syscall.
These are Reverie-level counter2 numbers (interception + one shared-Tool RPC per
syscall), so ptrace 14µs / KVM 14µs sit below the owner's determinism-inclusive
references; LiteInst 0.5µs is ~16× under gVisor systrap ~8µs. Ordering (patching
≪ ptrace ≈ KVM) is preserved and now contamination-controlled.

## Bottom line

The 1-CPU box **confirms** the prior attribution and **closes its only open
method caveat**: LiteInst in-guest patching is the perf leader at ~0.5µs/syscall,
~28× faster than ptrace/KVM; sequentialization is not a confound for these
single-threaded fixtures (measured, boxed≈unboxed). The lever for faster
*deterministic* runs remains the per-syscall Detcore coordinator RPC (see prior
report), not the patch mechanism.

## Files

- `raw/boxed-k1-summary.csv`, `raw/boxed-k1-overall.csv` — boxed K=1 (core 9)
- `raw/unboxed-summary.csv`, `raw/unboxed-overall.csv` — same-session unboxed
- `raw/boxed-k1-metadata.json` — harness metadata (SHA, host, params)
- `metadata.json` — this experiment's identity block

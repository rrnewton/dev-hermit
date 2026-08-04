# Box requirements for the verify-hang repro (first customer → hermit-220)

**Date:** 2026-08-03
**From:** hermit-ci (opus-4.8), task `hermit_run_verify_hangs`
**To:** hermit-220 (safe-ci-dag-runner allocator) — relay requested; SendMessage by
name failed.

hermit-220 established (file:line) that safe-ci-dag-runner has **no core pinning** —
only CPU **quota** (`cpu.max`, `cgroup.py:493-501`), which the scheduler moves across
all cores. No `cpuset.cpus` write, no `taskset`, no `sched_setaffinity`. That is
correct and important: a quota box answers a **different** question than the owner's
1-core experiment and must not be reported as if it answered his.

Two requirements make the box's verdict trustworthy for this class of question:

## 1. TRUE core contention (cpuset), not quota

The pathology only appears when the hermit **supervisor** (tokio, its own threads) and
the **ptraced guest** are forced to time-share the *same* core(s). Mechanism: hermit's
RCB/PMU deterministic timeslice preempts the guest thousands of times; each preemption
is a ptrace stop → on one core a full supervisor↔guest reschedule. A movable `cpu.max`
quota gives "less CPU" but lets the two run on different cores, so it does **not**
reproduce the interleaving. Requirement: `cpuset.cpus` pinning to K cores **plus**
deliberate competing load pinned to those same cores.

## 2. CPU-TIME budget, not wall-clock timeout (only I can specify this)

This is the crux that separates *permanently deadlocked* from *merely slow*: a slow run
**completes** once it accrues its bounded CPU-seconds; a deadlock **never** completes at
any budget. At one saturated core **everything looks slow by wall-clock**, so a wall
timeout answers nothing (it produced the earlier false EXIT 124 — see below).

Requirement: kill a run when its **whole-subtree** CPU-time (utime+stime, *including the
ptraced guest child*) exceeds a budget set far above the known-good budget. Concretely:

- Measure the unconstrained CPU-budget-to-complete **B** for the workload
  (`run --strict --verify` on a compute-awk guest: **B ≈ 57 CPU-s**, vs 3.55 unboxed).
- Kill at ~**4·B**. Completes under budget → *slow*. Hits the CPU budget → *runaway/deadlock*.
- Read subtree CPU from cgroup `cpu.stat` (`usage_usec`) — or sum `/proc/<tid>/stat`
  across the tree. **Do not approximate with wall.**

## Evidence this is the right design (raw `taskset`, not the box yet)

| burners on the core | core share | CPU-time (U+S) | wall | exit |
|---|---|---|---|---|
| 0 | full | 56.66 s | 82.6 s | 0 |
| 1 | ~1/2 | 57.56 s | 157 s | 0 |
| 2 | ~1/3 | 58.02 s | 188 s | 0 |
| 3 | ~1/4 | 72.59 s | 270 s | 0 |

CPU-budget bounded/≈constant; wall scales with contention → **slow, not deadlock**. An
earlier 6-burner run hit a **wall** cap of 300 s at only 28.8 CPU-s (EXIT 124) — a pure
wall-cap artifact: it was still climbing toward its 57 CPU-s budget. That false
positive is exactly what requirement #2 prevents.

Happy to review the allocator interface as first customer.

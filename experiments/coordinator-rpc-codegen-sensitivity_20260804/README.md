# Does rustc codegen matter for the Detcore coordinator-RPC floor? — NO

**Date:** 2026-08-04 · **Lane:** hermit-perf · **Task:** `detcore_strict_coordinator_rpc` (research-only)
**Host:** devbig014 (316 cores, shared/loaded) · **Hermit:** `c369be3f` · **Reverie:** `04a46b43` · release binary (Aug 3)

## Question (owner, reframed)

Hermit has no `[profile.*]` stanzas, so the coordinator inherits `codegen-units=16` and no LTO.
Does tuning codegen (opt-level / codegen-units / LTO) move the Detcore deterministic-mode
**per-syscall coordinator-RPC floor (~13.6 µs/syscall, idle-box)** that dominates every backend once the
in-guest patch fastpath has removed interception cost? **Reframe: answer the composition question first**
— *what is the floor made of?* If IPC / syscall / scheduling latency, codegen cannot reach it and
"no, and here's why" stops the lane chasing a knob that cannot move the number.

## Answer: NO — the floor is ~65% kernel sys time, ~35% user; and the untried knobs are already measured-inert

Two things settle it, and neither needs a rebuild:

1. **Composition (measured here):** the per-syscall coordinator cost is **~35% user CPU / ~65% system CPU**,
   with ≈0 idle-blocking (coordinator busy-polls → wait shows up as sys). Codegen touches only the user third.
2. **The knobs (measured same day, separate tasks):** hermit already ships `opt-level=3`; the only untried
   levers are `codegen-units=1` (**+2.2%, noise**) and LTO (**≤2% runtime, +35–164% compile = net loss**),
   and the LTO study found **~83% of supervisor CPU is kernel `sys` time LTO can't touch**. Best-case
   whole-floor effect ≈ `0.35 × ~5% ≈ 2%` — inside run-to-run noise (my N=10000 wall varied 0.77→0.98 s).

## Absolute anchors — µs per syscall (median of 5, K=2 box, N-slope, det − native)

`sched_yield` is a coordinator scheduling event (`hermit/detcore/src/lib.rs:703`); getpid (PassThrough) is
**not** and cannot probe the RPC — see §Vacuity. Marginal = slope N=10000→50000 (subtracts fixed startup);
native `sched_yield` marginal ≈ 0, so the whole cost below is determinism overhead.

| Per coordinator-RPC (`sched_yield`) | µs/syscall | fraction of CPU | codegen-addressable? |
| --- | ---: | ---: | --- |
| **user** | **22.0** | **34.5%** | partly — **but already opt3** |
| **sys** (kernel IPC / pidfd / nanosleep / openat) | **41.75** | **65.5%** | **No** |
| wall | 62.25 | (≈ user+sys → busy-poll, ~0 idle) | — |

**Absolute-value caveat:** 62 µs/call here is ~4.5× the owner's cited **13.6 µs** because that figure is from
an **idle-gated** flagship run and this box is shared/loaded (and busy-poll inflates sys under contention).
The **µs are load-inflated; only the ~35/65 user:sys split is treated as robust** — and it reproduced across
**five** independent cells (K=4 getpid, K=4 sched_yield, K=2 N=10000 single-point, K=2 N=50000 single-point,
K=2 N-slope median). Order of magnitude (tens of µs, sys-dominated) is stable.

## Evidence

### 1. The 1-CPU box livelocks the RPC — direct proof it is inter-thread, not compute
The owner-mandated 1-CPU box is the right instrument for a **single-threaded** instrumentation fastpath
(prior artifact: liteinst 496 ns boxed) but is **structurally inapplicable** to the coordinator RPC:
boxed to K=1 the `sched_yield` path **livelocks (rc=124 at N=2000, 45 s)**, while native `sched_yield` 1e6
on the same K=1 box is 0.16 s and the RPC completes fine at **K=2**. A compute-bound path merely runs
*slower* on one core; a **2-thread guest↔coordinator IPC ping-pong deadlocks** — so the cost is scheduling /
IPC, not user-space compute. (K=2 is the measured minimum core count for progress.)

### 2. CPU-time split (this run): ~35% user / ~65% sys, ≈0 idle
Median K=2 anchors above. `CPU/wall ≈ 102%` at both N → the coordinator is busy (busy-poll), not idle-blocked;
either way the 65% is kernel/scheduling, not instructions codegen emits.

### 3. Syscall composition (strace -f -c, corroborating)
Kernel time on the det path is dominated by `openat` (37.7%), `pidfd_open` (2000×), `clock_nanosleep` (2000×),
`munmap` — all kernel/scheduling ops. (This main-tree coordinator uses **pidfd + clock_nanosleep + openat**,
not the socket `sendto/recvfrom/epoll_wait` of the older flagship-tree artifact — different IPC mechanism,
same conclusion: sys-bound. strace was perturbed/`rc=1`, used qualitatively only.)

### 4. Vacuity of the obvious probe (resolves the pre-registered risk)
getpid is PassThrough — it does **not** round-trip the coordinator, so a getpid loop cannot exercise the RPC.
Any coordinator study must drive a **scheduling** syscall (`sched_yield`, `nanosleep`, futex, `wait4`).

### 5. The knobs are already measured (no rebuild needed)
- LTO: `experiments/ci-build-profile-lto_20260804/` — ≤2% runtime, net compile loss, 83% supervisor CPU is kernel.
- codegen-units=1: `experiments/inguest-handler-codegen-sensitivity_20260804/` — +2.2% (noise) even on the
  user-hot **handler**; that study explicitly scopes "LTO optimal" to the **supervisor**, i.e. this path.
- opt-level: already 3 (release default; confirmed no `[profile]` stanzas). opt0→3 is 3.5× **but** captured.

## What the lever actually is
Eliminate the per-syscall coordinator round-trip — in-process / shared-memory Detcore fastpath, in-guest RCB
read via `rdpmc` (`inguest-rcb-read-needs-rdpmc-not-ptrace-mmap-fastpath`). That attacks the 65% kernel floor
and the ~62 µs itself (fastpath interception is 0.58 µs — ~100× below the RPC), where codegen's ~2% cannot.

## Caveats / limits
- **Absolute µs load-inflated** (shared 316-core box); only the ~35/65 split is relied on (reproduced 5×).
- **No new codegen A/B built** — deliberately: the composition bounds the ceiling <~5% and the cu1/LTO A/B
  was already run the same day on supervisor + handler paths. A rebuild would spend hours to reconfirm noise.
- **Heisenbug (out of scope, scheduler lane):** getpid loop under default-deterministic liteinst intermittently
  hangs at N=10000 and N≥1e5 while N=20000/40000 complete — scheduling livelock, not per-call cost. Related:
  `scheduler-vtime-jump-unproductive-pollers`, `demo5-spin-unbounded-burnout-missing`.

## Reproduce
```
cc -O2 -o src/yield_loop src/yield_loop.c
python3 measure.py     # K=2 box, 5 reps, N=10000/50000 slope, det & native → median-anchors.json
# 1-CPU livelock check: run-on-k-free-cores.py 1 -- hermit run --backend liteinst -- src/yield_loop 2000  (hangs)
```
Raw: `results.csv`, `median-anchors.json`. Provenance: `metadata.json`. Driver: `measure.py`. Fixture: `src/yield_loop.c`.

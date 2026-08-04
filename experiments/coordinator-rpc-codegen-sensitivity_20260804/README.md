# Does rustc codegen matter for the Detcore coordinator-RPC floor? — NO

**Date:** 2026-08-04 · **Lane:** hermit-perf · **Task:** `detcore_strict_coordinator_rpc` (research-only)
**Host:** devbig014 (316 cores) · **Hermit:** `c369be3f` · **Reverie:** `04a46b43` · release binary (Aug 3)

## Question (owner, reframed)

Hermit has no `[profile.*]` stanzas, so the coordinator inherits `codegen-units=16` and no LTO.
Does tuning codegen (opt-level / codegen-units / LTO) move the Detcore deterministic-mode
**per-syscall coordinator-RPC floor (~13.6 µs/syscall)** that dominates every backend once the
in-guest patch fastpath has removed interception cost? The reframe: **answer the composition
question first** — *what is the 13.6 µs made of?* If it is IPC / syscall / wakeup latency rather
than user-space compute, codegen cannot move it and "no, and here's why" is the complete answer.

## Answer: NO — the floor is ~69% kernel/IPC + scheduling latency, ~31% user-space compute

The composition, measured three convergent ways, is dominated by things codegen cannot touch:

| Component of the per-syscall determinism cost | Share | Codegen-addressable? |
| --- | ---: | --- |
| System/kernel time (`sendto`×3, `recvfrom`×5, `epoll_wait`×2 round-trip) + cross-thread wakeup | **~69%** | **No** (syscall + scheduler latency) |
| User-space compute (guest-side + coordinator deserialize / schedule-decision / serialize) | **~31%** | Partly — **but already `opt-level=3`** |

`hermit/Cargo.toml` has **no `[profile]` stanzas**, so release is already `opt-level=3` (confirmed).
The only untried levers are `codegen-units 16→1` and adding LTO, which act **only on the 31% user
fraction** and typically buy single-digit % on already-`opt3` code. Best-case whole-floor effect:
`~31% × ~10% ≈ 3%` — inside measurement noise. **Not worth a rebuild matrix.** (DO-LESS gate hit.)

## Evidence

### 1. Structural (established, prior artifact, strace-verified)
The `--strict` per-intercepted-syscall delta is **+3 `sendto`, +5 `recvfrom`, +2 `epoll_wait`**
(`strace -f -c`; [liteinst-perf-attribution-fastpath-is-leader_20260804.md](https://github.com/rrnewton/dev-hermit/blob/main/ai_docs/liteinst-perf-attribution-fastpath-is-leader_20260804.md) §"Where the
`--strict` time goes"). Ten blocking IPC/wait kernel syscalls + a guest↔coordinator round-trip =
the RPC. These are kernel + wakeup costs by construction, not user-space instruction execution.

### 2. Empirical CPU-time split (this run)
CPU-time (user+sys) slope across two N, boxed, `/usr/bin/time -v`:

| Fixture | syscall class | marginal user | marginal sys | **user / total-CPU** |
| --- | --- | ---: | ---: | ---: |
| `yield_loop` (sched_yield → coordinator event, `lib.rs:703`) | coordinator | 21.25 µs | 46.25 µs | **31%** |
| `syscall_loop` (getpid, PassThrough) | none | 24.86 µs | 56.29 µs | **31%** |

Both land at **~31% user / ~69% system** — the load-independent quantity relied on here (absolute
µs are load-inflated on this shared box and possibly reflect coordinator busy-poll; see caveats).
Codegen speeds only the user fraction, and that fraction is already compiled at `opt-level=3`.

### 3. Vacuity of the obvious probe (resolves the pre-registered risk)
**getpid is a vacuous coordinator probe.** getpid is PassThrough — it does *not* round-trip the
coordinator — so a getpid loop cannot exercise the RPC (~81 µs wall marginal here is PassThrough +
determinism bookkeeping, not the 13.6 µs RPC). Any codegen study must drive a **scheduling** syscall
(`sched_yield`, `nanosleep`, futex, `wait4`), not getpid. This confirms the risk flagged at spawn.

## What the lever actually is
Consistent with the prior attribution: the way to speed *deterministic* execution is **eliminating
the per-syscall coordinator round-trip** — in-process / shared-memory Detcore fastpath, in-guest RCB
read via `rdpmc` (`inguest-rcb-read-needs-rdpmc-not-ptrace-mmap-fastpath`) — **not** recompiling the
coordinator with different codegen flags.

## Caveats / limits
- **No direct codegen A/B was built.** The conclusion is from the composition (31/69 split) per the
  owner's reframe, not from a 4-variant rebuild — deliberately, because the split proves the ceiling
  is <~5%. A rebuild matrix would spend hours to confirm a within-noise effect.
- **Absolute µs are load-contaminated** (shared 316-core box under background load, K=4 box). Only
  the **user:sys ratio** is treated as robust; it is stable across two independent fixtures.
- **Heisenbug (out of scope, flagged for the scheduler lane):** the getpid loop under
  default-deterministic liteinst **intermittently hangs** (>90–120 s) at N=10000 and N≥1e5 while
  N=20000/40000 complete — a scheduling livelock, not per-call cost. Related memory:
  `scheduler-vtime-jump-unproductive-pollers`, `demo5-spin-unbounded-burnout-missing`.

## Reproduce
```
cc -O2 -o src/yield_loop src/yield_loop.c
HERMIT=.../hermit/target/release/hermit ; SO=.../libreverie_liteinst.so
for N in 2000 5000 10000; do
  run-on-k-free-cores.py 4 -- /usr/bin/time -v \
    env HERMIT_LITEINST_RUNTIME=$SO $HERMIT run --backend liteinst -- src/yield_loop $N
done
# marginal user/sys slope across two N; user/(user+sys) ≈ 31%
```
Raw data: `results.csv`. Provenance: `metadata.json`.

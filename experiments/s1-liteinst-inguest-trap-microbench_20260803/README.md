# S1: LiteInst in-guest trap-round-trip micro-benchmark (Mode A vs ptrace)

**One-line result:** On the instrumentation trap-round-trip axis — the *only*
axis an in-guest backend can win — the LiteInst direct-`Backend` in-guest path
(**Mode A**) beats a ptrace round-trip by **31.2x**, and this is a
**conservative** figure because the in-guest arm carries full `strace`
per-hook I/O it did not have to.

**This is the S1 gate for the unification question, and S1 PASSES.**

## Provenance

- **UTC run:** 2026-08-03 (~18:40Z)
- **Run ID:** `s1-liteinst-inguest-trap-microbench_20260803`
- **Host:** `devbig014` (short label only), kernel
  `6.18.39-0_fbk0_hardened_0_ga43d5727b443`, AMD EPYC 9D85 (316 CPUs, **shared**)
- **Reverie build SHA (measured .so):**
  `d2fb9a055693bec30e8d48333c5694050b22e869`
- **Reverie doc SHA (Mode A/B labels, doc-only, does not affect the .so):**
  `1787127266ba88e566242a1c0b8dc76913b1c489`
  on branch `codex/s1-liteinst-inguest-trap-microbench`
- **Hermit primary SHA:** `bfb0a9ef1c303d1977f5f02903b70cc93e514cb5`
- **Parent SHA:** `b00c3505afb9c4d1faacb7b34b749b6aecbeb32a`
- **Toolchain:** `rustc 1.99.0-nightly (26ae60a9e 2026-07-28)`
- **Raw data:** [`results.csv`](results.csv) · **Harness:** [`harness.sh`](harness.sh)
  · **Metadata (full SHAs, digests):** [`metadata.json`](metadata.json)

## The two cost axes (why this benchmark is shaped this way)

Any deterministic backend pays two separable costs per intercepted syscall:

- **(a) Sequentialization** — park the thread and RPC to the global scheduler.
  Present in *every* backend (ptrace, DBI, KVM, in-guest alike). It is a
  property of the determinism model, not the interception mechanism, so an
  in-guest backend **cannot** win it.
- **(b) Instrumentation trap round-trip** — the cost of getting *from* the
  syscall site *to* the tool callback and back. Ptrace pays a full
  kernel→tracer→kernel context-switch round-trip here; an in-guest hook pays a
  near-local call. **This is the only axis in-guest can win**, so S1 isolates
  it.

`getpid` is chosen deliberately: it requires **no scheduling decision**, so for
this syscall **axis (a) = 0**. What remains in the delta is pure axis (b).

## Methods

- **Isolation:** the whole process tree runs inside one
  `systemd-run --user --scope -q -p AllowedCPUs=3` cgroup. **K=1**, CPU set
  `{3}`, **no per-task pinning** (kernel schedules within the 1-CPU set). K=1
  holds axis (a)'s contention constant across arms. This is **not** exclusive
  hardware — the 316-CPU box is shared; see caveats.
- **Guest:** `getpid_loop.c`, a zero-I/O loop `volatile long acc += s1_getpid()`
  over `N` iterations. `s1_getpid` is a **dedicated padded asm site**
  (`mov $NR,%eax; site: syscall; .fill 6,1,0x90; ret`); libc's generic
  `syscall()` wrapper is not patchable by LiteInst. Built
  `cc -std=gnu11 -O0 -fno-pie -no-pie`.
- **Arms:**
  - `native` — guest run directly, no instrumentation.
  - `liteinst` — **Mode A**, launched the canonical way:
    `REVERIE_LITEINST_PRELOAD=libreverie_liteinst.so reverie-liteinst-strace getpid_loop N`.
    First syscall-site execution hits seccomp/SIGSYS discovery (traps=1); the
    dispatcher installs a replace-first in-guest hook and all later calls enter
    the normal-context tool callback with **no ptrace**. The `strace` tool
    formats + writes one line per hook, so this arm is a **conservative upper
    bound** on axis (b): the null-hook path (proven to exist via the
    `trap_count_guest` counters: `calls=32 traps=1 hooks=32`) is faster.
  - `ptrace` — `counter2 -- getpid_loop N`, one reverie-ptrace round-trip per
    syscall.
- **Sampling:** 2 warmups + 10 measured reps per point; wall-clock
  `date +%s%N` deltas. **Two-point slope** per arm removes fixed per-process
  startup: `ns/syscall = (median(T(N_b)) - median(T(N_a))) / (N_b - N_a)`.
  Points: native {5e5, 2.5e6}, liteinst {1e5, 6e5}, ptrace {1e4, 5e4}.
- **Statistic:** median primary; MAD reported below.

## Evaluation

Each arm answers one question: `native` fixes the workload's own cost;
`liteinst` (Mode A) and `ptrace` differ from native and from each other **only**
in how a syscall reaches the tool. Since axis (a)=0 for `getpid`, the
liteinst-vs-ptrace ratio *is* the axis-(b) ratio.

## Results

Per-syscall cost (two-point slope, median-based; n=10 per point):

| Arm | ns / syscall | vs native | MAD (larger-N point) |
| --- | ---: | ---: | --- |
| native | 64.3 | 1.0x | 0.60 ms / 2.5e6 |
| **liteinst (Mode A, in-guest, incl. strace I/O — UPPER BOUND)** | **845.7** | **13.1x** | 1.07 ms / 6e5 |
| ptrace (reverie-ptrace + counter) | 26,393.7 | 410.3x | 14.9 ms / 5e4 |

**Axis-(b) gate:** `ptrace / liteinst = 31.2x`. The in-guest path wins the
instrumentation round-trip axis by a factor of 31, and by more once `strace`
I/O is removed from the in-guest arm.

- **Instrumentation-only slowdown (reported first, per protocol):** Mode A is
  13.1x native — but this is axis (b) *plus* strace formatting/write I/O, not a
  clean instrumentation-only figure; the mechanism cost is strictly lower.
- **Sequentialization increment:** **not measured here by construction** —
  getpid has axis (a)=0, so this benchmark deliberately isolates (b) and says
  nothing about (a). Any full-backend projection MUST add (a) separately.

## What S1 does and does NOT prove

**Proves:** the in-guest interception *mechanism* (Mode A) beats a ptrace
round-trip on axis (b) by ~31x, conservatively. The mechanism advantage that
motivates in-guest unification is real and measured.

**Does NOT prove:**

- a full-Detcore fast path on Mode A — that is **build-gated**: Mode A's
  `tool_host.rs` stubs clock/timer/scheduler as `Unsupported`; full Detcore has
  never run on Mode A;
- anything about axis (a) (sequentialization) — excluded by design;
- that a full Detcore-on-ptrace tool equals the counter tool — Detcore would add
  tool work on top of the round-trip (making ptrace look *worse*, not better).

## Mode A / Mode B mapping (audit discrepancy D3, fixed)

The "Mode A" / "Mode B" labels are analysis shorthand that `BACKENDS.md` did not
name. Fixed in commit `1787127` (this branch):

- **Mode A** = "LiteInst, direct `Backend`" (`BACKENDS.md`) — in-guest, no
  per-syscall ptrace; SIGSYS discovery once, then in-guest hooks. **This is what
  S1 measured.**
- **Mode B** = "LiteInst, ptrace-owned hybrid" =
  `reverie-liteinst::backend::run_host_with_preload<T>` (`backend.rs:208`);
  hermit's only wired liteinst-Detcore path (`hermit-cli/src/lib.rs:1528`).
  **Untouched by S1.**
- Not to be confused with `reverie-preload`'s `HybridPtrace`
  (`lifecycle.rs:95`), a separate, *unimplemented* lifecycle stub (returns
  `Unsupported`).

## Reproduction

```bash
# build reverie at d2fb9a0 (workspace: reverie/, target/debug)
#   cargo build -p reverie-liteinst --bins           # libreverie_liteinst.so, reverie-liteinst-strace
#   cargo build -p reverie-ptrace --bin counter2     # ptrace counter arm
# build the guest:
#   cc -std=gnu11 -O0 -fno-pie -no-pie -o /tmp/s1bench/getpid_loop /tmp/s1bench/getpid_loop.c
systemd-run --user --scope -q -p AllowedCPUs=3 bash harness.sh > results.csv
# then: two-point slope per arm on the non-warmup rows (see metadata.json)
```

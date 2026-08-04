# JVM power-to-weight under `hermit --strict --verify`

**Date:** 2026-07-30 · **Author:** impl agent, opus-4.8 · **Task:**
`small-jvm-jit-runtime-compat`

**Question.** javac is the wrong every-commit CI gate — it is slow and only
reaches L1. Which *small* JVM programs give the most **syscall + JIT coverage
per wall-second** under `hermit run --strict --verify`, so we keep meaningful
JVM runtime/JIT validation on every commit cheaply?

**TL;DR.** Under Hermit, the JVM's *unique* syscall surface is **saturated by
startup** — every real program hits ~51 distinct syscalls, the same set
`java -version` already uses. The only programs that add *new syscall types* are
I/O programs (file, socket), and they are the most expensive. So the
every-commit lever is not "more unique syscalls"; it is **JIT tiers (C1/C2) and
scheduler/concurrency determinism per second**. The recommended every-commit
set is **`Hello` + `JitHotLoop` + `ThreadCounter`** (~38 s of `--verify` wall,
all L2), with `HashMapString`, `NioFile`, `NioSocket`, and `javac` demoted to an
occasional/nightly tier.

---

## Setup

- **Host:** devbig014 (316 cores), kernel 6.18.39; **JDK:** OpenJDK 1.8.0_492
  (Temurin) Server VM, tiered compilation ON; **strace** 6.12.
- **Hermit:** `hermit/target/release/hermit` @ primary `main`
  `9c964fce16ab60c3eadecc557fa2844399854f06` (ptrace backend).
- **Native pass** (JIT ON): `strace -f -c` for syscalls; `-XX:+PrintCompilation`
  for JIT tiers (tier 4 = C2, tiers 1-3 = C1); min of 3 runs for wall.
- **Hermit pass** (JIT ON):
  `hermit --log=info run --strict --verify --no-virtualize-cpuid -- java <args>`,
  **RCB preemption default ON** (see finding #1), 120 s timeout.
- **Caveat:** the hermit pass ran on a host saturated by other agents' Hermit
  jobs; absolute wall times are **inflated upper bounds**. Relative ranking is
  the reliable signal. Raw data:
  `experiments/jvm_power_to_weight_20260730/`.

## Results — the matrix

Sorted by `--verify` wall (cheapest first). `h_wall` = two-run `--verify` wall
seconds. `uniq/total` = distinct/total syscalls (strace, threads followed).
`JIT` = total compiles, `C2` = tier-4 compiles. `JIT/s` and `sys/s` are per
hermit-second (coverage-per-weight).

| program | h_wall (s) | verdict | uniq sys | total sys | JIT | C1 | C2 | JIT / s | totalsys / s |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| `java -version` | 2.1 | L2 ✅ | 52 | 10345 | 0 | 0 | 0 | 0.0 | 4995 |
| `JitHotLoop` | 6.0 | L2 ✅ | 51 | 11492 | 19 | 15 | **3** | 3.2 | 1928 |
| `Hello` | 8.7 | L2 ✅ | 51 | 11221 | 11 | 10 | 0 | 1.3 | 1293 |
| `GcStress` | 12.1 | L2 ✅ | 51 | 11253 | 11 | 10 | 0 | 0.9 | 928 |
| `HashMapString` | 19.5 | L2 ✅ | 51 | 16385 | **363** | 260 | **43** | **18.6** | 839 |
| `ThreadCounter` | 23.2 | L2 ✅ | 51 | 14836 | 248 | 183 | 5 | 10.7 | 640 |
| `NioFile` | 75.8 | L2 ✅ | **55** | 13943 | 87 | 80 | 4 | 1.1 | 184 |
| `NioSocket` | >120 | ⏱ timeout | **60** | 15719 | 247 | 183 | 5 | — | — |
| `javac` (baseline) | ~L1, slow | L1 only | — | — | (compiler) | | | — | — |

*(`javac` and `java Hello` are the requested baselines: javac = high coverage
but slow and only L1; `Hello` = fast but trivial. `javac`'s numbers are the
existing `app_strict_verify.rs` L1 result — a build-in-place defeats `--verify`'s
two-run diff; it is not a per-second candidate.)*

## Findings

**1. `--max-timeslice=disabled` livelocks a JIT-on JVM; keep RCB preemption on.**
The existing `java_hello`/`java_threads` helper passes `--max-timeslice=disabled`.
With preemption disabled Hermit never interrupts a CPU-bound guest thread, so
the JVM's GC/JIT/dispatcher threads starve and the guest deadlocks (measured:
`Hello --verify --max-timeslice=disabled` → rc=124, full 120 s timeout, stuck in
Run1). Because the RCB count is itself deterministic, dropping that flag keeps
`--verify` at **L2**. Every result above uses default preemption. The existing
`java_hello`/`java_threads` tests share this latent livelock on many-core/PMU
hosts and are a follow-up cleanup candidate.

**2. Unique syscall surface is saturated by JVM startup.** `java -version`
already touches 52 distinct syscalls. `Hello`, `JitHotLoop`, `GcStress`,
`HashMapString`, and `ThreadCounter` add **zero** new syscall *types* — the
mmap/mprotect/futex/clone/madvise surface is all exercised during class-loading
and runtime init before any user bytecode runs. Only I/O adds new types:
- `NioFile` adds `+{fsync, pwrite64, getsockname, socketpair}`
- `NioSocket` adds `+{accept, bind, listen, connect(recvfrom), setsockopt, shutdown, dup2, getsockname, socketpair}`

  → If the goal is **unique-syscall breadth**, a single socket program is worth
  more than any number of compute programs — but it is also the most expensive.

**3. `java -version` is a surprisingly rich *syscall* probe but a JIT dead end.**
It hits 52 unique / 10345 total syscalls (≈ `Hello`) yet triggers **0** JIT
compiles and runs **no user bytecode**. Per owner guidance it is *measured but
not recommended*: version probes previously flooded the suite, and it validates
nothing about the runtime executing real Java. Prefer a real hello-world.

**4. JIT density is dominated by core-library code, not by hand-rolled hot
loops.** `HashMapString` triggers the most compilation (363 total, **43 C2**)
and the highest JIT/second (18.6) because `String.hashCode`, `HashMap`, and
`TreeMap` get hot. `ThreadCounter` is second (248, 5 C2). `JitHotLoop`'s
hand-written hot method reliably reaches **C2 (3 tier-4 compiles)** at the
lowest absolute cost (6.0 s) — it is the cheapest program that *guarantees* a
C1→C2 promotion, which is exactly what a JIT smoke test wants.

**5. `NioSocket` did not livelock — it is scheduler-heavy.** Its two runs each
produced **4.58 M** scheduler messages (4.04 M DETLOG/COMMIT); the 120 s timeout
hit *during `--verify`'s log comparison*, not during execution. Loopback TCP
generates enormous deterministic-scheduler traffic. It is the widest
unique-syscall program but far too heavy for every-commit CI.

## Recommendation — every-commit CI set

Because unique syscall breadth saturates at startup, the every-commit set should
maximize **JIT tier coverage + concurrency/scheduler determinism per second**,
not chase syscall types. All three below reach **L2** and run real Java bytecode
(no version probes, per owner guidance):

| tier | programs | why | ~cost |
|---|---|---|---|
| **every commit** | `Hello`, `JitHotLoop`, `ThreadCounter` | real-code startup/class-load/GC-init smoke (`Hello`) + guaranteed C1→C2 JIT promotion at lowest cost (`JitHotLoop`) + clone/futex concurrency under the deterministic scheduler (`ThreadCounter`) | ~38 s |
| **optional swap** | `HashMapString` for `ThreadCounter` | if you want maximum JIT/C2 density (363 compiles, 43 C2) over concurrency coverage | ~20 s |
| **occasional / nightly** | `NioFile`, `NioSocket`, `javac` | the only sources of file/socket syscall *types* (`NioFile`/`NioSocket`) and compiler coverage (`javac`, L1) — each too slow for every commit | 76 s / >120 s / slow |

**Minimal 2-program fallback** (tightest budget, ~15 s): `Hello` + `JitHotLoop`
— covers real startup + guaranteed C1/C2 JIT. Add `ThreadCounter` whenever the
budget allows, because this is a deterministic-*scheduler* project and
`ThreadCounter` is the cheapest program that stresses the scheduler under thread
contention.

**Do not** put `java -version` (or any `--version`/`--help` probe) in the CI set:
rich syscalls, but zero JIT and zero user bytecode.

## Relationship to prior work

The four programs from PR
[#1163](https://github.com/rrnewton/hermit/pull/1163) (`ThreadCounter`,
`GcStress`, `JitHotLoop`, `HashMapString`) are exactly the compute/thread/GC/JIT
corpus this analysis ranks; three of the recommended every-commit set are drawn
from it (plus the pre-existing `Hello`). This analysis adds the coverage/second
justification, the `NioFile`/`NioSocket` I/O-surface data, and the finding that
JIT should be **on** with **default preemption** (not `-Xint` +
`--max-timeslice=disabled`).

## Reproduce

`experiments/jvm_power_to_weight_20260730/` — `measure_native.sh`,
`measure_hermit.sh`, sources, and raw `results/`.

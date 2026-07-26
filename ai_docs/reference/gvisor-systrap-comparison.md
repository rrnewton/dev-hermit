# gVisor `systrap` vs. Hermit/Reverie's backends — techniques & benchmarks

**Author:** [impl agent, opus-4.8] (task `impl-gvisor-systrap-writeup`), 2026-07-25.
**Companion deep-dive:** `ai_docs/gvisor-systrap-analysis.md` (systrap internals, source
map). **Source material:** tasks `research-gvisor-systrap` (hermit-170) and
`impl-gvisor-benchmark-v2` (hermit-178); `PROJECT_VISION.md`.

This document compares the syscall-interception technique gVisor's **systrap**
platform uses to Hermit/Reverie's six backend efforts (ptrace, DBI/DynamoRIO,
KVM, SaBRe, e9patch, LiteInst), gives a side-by-side feature matrix, and reports
steady-state benchmark numbers.

> **Terminology (per `hermit/CLAUDE.md`).** A *backend* is a complete path that
> loads the shared Detcore tool as `Detcore<XxxGuest>` through Reverie. By that
> strict definition **e9patch is not a backend** (it is binary-rewriting
> *preprocessing* used with the ptrace backend), and SaBRe/LiteInst are
> experimental. The task lists six "backends"; below each is labeled with its
> true category and maturity so the classification stays honest.

---

## 1. gVisor `systrap` in one page

systrap replaced ptrace as gVisor's default platform in mid-2023 because ptrace
costs 4+ context switches per guest syscall. Its interception is **three layered
mechanisms, not one** (fail-closed floor + opportunistic fast path):

1. **Primary trap — Syscall User Dispatch (SUD)**, `PR_SET_SYSCALL_USER_DISPATCH`,
   kernel ≥ 5.11. Any `syscall` instruction executed *outside* the stub code
   range delivers `SIGSYS`. Per-thread property → **fail-closed**: every syscall
   traps regardless of how the code was mapped (dlopen/JIT/static/hand-asm). No
   BPF evaluation, cheaper than seccomp.
2. **Fallback trap — seccomp `SECCOMP_RET_TRAP`** for kernels < 5.11 → also
   `SIGSYS`.
3. **Fast path — `usertrap` in-place binary patching.** The `SIGSYS` handler
   recognizes the 7-byte `mov sysno,%eax; syscall` pattern and rewrites it to
   `jmp *trapAddr`, so patched sites jump straight into the stub handler with
   **no signal at all** — just a function call. Patching is only an optimization
   *on top of* a complete trap floor.

**Execution/perf model — decoupling + dual spinning (the real innovation):**
three decoupled objects — an `AddressSpace` (a forked *stub process* with its own
page tables), a `thread_context` (one guest thread's register+FP state living in
**shared memory**), and a `sysmsg` *host worker thread*. M guest contexts are
multiplexed over N worker threads via a shared-memory ring (`contextQueue`). On
the hot path, when a CPU is free there are **no syscalls**: the sentry writes
regs to shared memory and enqueues; a stub thread **spins** to pick it up; on a
guest syscall the patched site jumps to the handler, writes the result to shared
memory, and the sentry side **spins** to observe it. It falls back to
`FUTEX_WAIT/WAKE` only after a deep-sleep timeout when CPUs are saturated.

**Root?** Not host root, but **not pure-unprivileged either**: systrap requires
`CAP_SYS_PTRACE` (for stub bootstrap `PTRACE_ATTACH`, full reg/FP transfer via
`PTRACE_GETREGSET/SETREGSET`, and a `PTRACE_SYSEMU` slow-path executor).
`--rootless` obtains it inside an unprivileged **user namespace** (needs
`unprivileged_userns_clone=1`). So it is *not* an LD_PRELOAD-style zero-privilege
approach.

**vDSO — mandatory, easily forgotten.** `clock_gettime`/`gettimeofday` on the
fast path execute *no* `syscall` instruction, so **neither SUD nor seccomp nor
ptrace can trap them**. gVisor ships its **own vDSO** that reads time from a
sentry-maintained shared **param page** via a seqlock (pure userspace read), and
falls back to a real trapped `sys_clock_gettime` when a clock is not ready. Any
non-ptrace interception backend that virtualizes time **must** replace/neutralize
the guest vDSO or determinism is a lie. *(This is exactly the DBI clock gap —
`rrnewton/hermit#705`: reverie-dbi lacks the vDSO neutralization reverie-ptrace's
`vdso.rs` performs, so `date` reads DBI's zero-based TSC → 1970.)*

**fork/exec/signals are the sentry's job, not the platform's.** The platform only
exposes `NewAddressSpace/NewContext/Switch/MapFile`; guest clone/execve are
ordinary trapped syscalls the userspace kernel handles, and guest signals are
*synthesized* by editing the guest sigframe in shared memory — host signals
(SIGSYS/SEGV/BUS/FPE/TRAP/ILL) are only the trap/fault mechanism.

**Determinism caveat:** systrap's M:N multiplexing and spinning are
**nondeterministic by design** (built for throughput). The reusable parts for
Hermit are the *transport* (SUD trap + shared-mem handshake + vDSO param page),
**not** its opportunistic scheduler — that seam must be filled by Detcore's
deterministic scheduler.

---

## 2. Hermit/Reverie's six backend efforts

All real backends run the **same** Detcore determinism code as `Detcore<XxxGuest>`
through Reverie; the guest impl differs. Maturity uses the backend-reality rubric
(`.llms/skills/backend-reality-reviewer`): **B0** crate compiles · **B1** Guest
trait partial · **B2** runs trivial programs through real Detcore · **B3** ≥50% of
the ptrace strict-verify corpus · **B4** 100% = done.

### 2.1 ptrace — *the reference backend* (default, most mature)
- **Interception:** classic `PTRACE_SYSEMU`/seccomp stops; 1 tracer : 1 tracee,
  stop/continue per event. Neutralizes the vDSO by patching it
  (`reverie-ptrace/src/vdso.rs`) so time calls become trapped syscalls.
- **Perf model:** 4+ context switches per syscall → highest per-syscall cost of
  any backend (see §4, ~40 µs/syscall).
- **Maturity:** the correctness reference (≈B4); every other backend is measured
  against it. Requires hardware PMU (RCBs) for deterministic preemption.

### 2.2 DBI / DynamoRIO — `Detcore<DbiGuest>` (real backend, **B2**)
- **Interception:** dynamic binary translation; DynamoRIO retranslates guest code
  and intercepts syscalls; Detcore is loaded **in-process** (client injected into
  the guest), so local↔global "RPC" is a direct in-process call, no IPC.
- **Perf model:** cheap per-*syscall* (~1.4 µs) but pays a per-*branch*
  translation cost that dominates compute-bound guests (sqlite 100k: **21× / 63 s**).
- **Maturity:** B2 — trivial programs run deterministically through real Detcore;
  known gap: **no vDSO neutralization → clock not virtualized** (`#705`, `date`=1970).
  Remaining B3 blockers (reverie-side): clone/exec native lifecycle (reverie #31),
  timers/RCB preemption, signals, ppid.

### 2.3 KVM — `Detcore<KvmGuest>` (real backend; flagship priority)
- **Interception:** the **gVisor model** — guest runs in a VM (ring3→ring0), all
  syscalls trap to a userspace "kernel," here **Detcore as the OS**.
- **Perf model (this repo's `reverie-kvm`):** ~29 µs/syscall in the v2 benchmark
  and currently **cannot run** find/dd/tar (ENOSYS after ~50–80 syscalls) — an
  immaturity limit, not an interception-cost floor. *(Distinct from gVisor's own
  KVM platform, which is ~1 µs/syscall; see §4.)*
- **Maturity:** real `Detcore<KvmGuest>` reading the user ELF has landed; echo/
  true/cat pass L2 on main; broad compatibility is the long-tail work.
  PROJECT_VISION names KVM the intended flagship.

### 2.4 SaBRe — in-process ELF rewriting (experimental, **fail-open**)
- **Interception:** static in-process ELF/PLT rewriting of syscall sites.
- **Perf model:** fastest measured (~1 µs/syscall) — no trap, direct call.
- **Coverage risk:** **fail-open** — misses dlopen/JIT/static/hand-asm syscall
  sites → *silent* nondeterminism. This is the critical axis where systrap's
  fail-closed SUD floor is architecturally superior.
- **Maturity:** experimental.

### 2.5 e9patch — static binary rewriting **preprocessing** (*not a backend*)
- **What it is:** ahead-of-time static rewriting of the guest binary; the rewritten
  binary then runs under the **ptrace backend**. A CLI spelling like
  `--backend=e9patch` does not make it load Detcore itself.
- **Coverage risk:** same fail-open class as SaBRe for code it cannot see
  statically.
- **Maturity:** preprocessing/vision; report results as "e9patch preprocessing
  with the ptrace backend," never as an "e9patch backend."

### 2.6 LiteInst — dynamic instruction hooking (experimental)
- **What it is:** dynamic hooking of arbitrary instructions (syscall/cpuid/rdtsc/
  rdrand) via instruction-punning (liteinst2), intended to ride **LD_PRELOAD** to
  infect the guest and its children, with a ptrace supervisor as backstop for
  corner cases. A LiteInst compatibility path landed recently (#688).
- **Maturity:** experimental; closest in spirit to a "reverie-preload" future and
  to systrap's layered idea (fast in-process hook + supervisor floor).

---

## 3. Side-by-side comparison

| Feature | gVisor systrap | ptrace | DBI/DynamoRIO | KVM (`Detcore<KvmGuest>`) | SaBRe | e9patch | LiteInst |
|---|---|---|---|---|---|---|---|
| Category | platform (nondet) | **real backend** | **real backend** | **real backend** | experimental backend | *preprocessing (not a backend)* | experimental |
| Interception | SUD/seccomp + patch | ptrace stops | dynamic translation | VM trap (ring3→0) | static in-proc rewrite | static AOT rewrite → ptrace | dynamic instr. hook |
| Trap completeness | **fail-closed** | fail-closed | fail-closed | fail-closed | **fail-open** | **fail-open** | fail-open + ptrace backstop |
| Needs ptrace/root | CAP_SYS_PTRACE in userns | yes (ptrace) | no ptrace | KVM device | no | via ptrace | no (backstop optional) |
| Per-syscall cost | ~8 µs (this bench) | **highest** ~40 µs | low ~1.4 µs | ~29 µs (repo kvm) | **lowest** ~1 µs | ≈ ptrace | n/a (untested here) |
| Compute overhead | low | low | **high (per-branch)** | low | low | low | n/a |
| vDSO/time handled | **yes (own vDSO+param page)** | yes (`vdso.rs` patch) | **NO (#705)** | via VM | needs work | needs work | needs work |
| Loads Detcore | no (its own sentry) | yes | yes | yes | yes | no (runs under ptrace) | intended |
| Deterministic sched | no (M:N spinning) | yes (Detcore) | yes (Detcore) | yes (Detcore) | yes | yes (via ptrace) | intended |
| Maturity | production (nondet) | **reference ≈B4** | **B2** | real, compat long-tail | experimental | preprocessing | experimental |

---

## 4. Benchmark results (steady-state syscall interception, v2)

Source: `impl-gvisor-benchmark-v2` (hermit-178), experiment
`experiments/gvisor-reverie-benchmark_20260725/`. Host AMD EPYC 9D85, pinned
CPU112, 2 warmup + 9 measured runs, **median**; host load ~300–400 so **treat
ratios, not absolutes, as signal**. Provenance: gVisor `8eb8f9e0` (runsc sha256
`609fa54e`); reverie base `d0bf6cc8`; rustc 1.96.0.

**Purest raw trap cost — `getpid-3s` (40,000,000 `getpid()`), amortized
µs/syscall = (median − native)/count, and slowdown ×:**

| Backend | µs/syscall | slowdown × |
|---|---|---|
| reverie-sabre | 0.99 | 10.5 |
| gvisor-kvm | 1.01 | 10.8 |
| reverie-dbi | 1.42 | 14.8 |
| gvisor-systrap | 8.08 | 79 |
| reverie-kvm | 29.31 | 284 |
| **reverie-ptrace (default)** | **40.51** | **392** |

Headline: **the default backend (reverie-ptrace) is the slowest per syscall**
(~40 µs), ~40× costlier than SaBRe/gVisor-KVM/DBI (~1 µs). gVisor-systrap (~8 µs)
sits in between. The two "KVM"s differ hugely: **gVisor-kvm ~1 µs vs reverie-kvm
~29 µs** — the repo's KVM backend is immature, not a KVM-cost floor.

**Real workloads — per-syscall overhead (ns) and slowdown ×** (native median in
parens): getpid-3s (4.14 s) · find /usr -type f (7.61 s, exit 1) · dd bs=1
count=15M = 30M ops (3.35 s) · tar (3.14 s) · sqlite-100k (compute-bound, ~167
syscalls):

| Workload | sabre | dbi | gvisor-systrap | gvisor-kvm | reverie-kvm | ptrace |
|---|---|---|---|---|---|---|
| getpid-3s | 0.99 µs (10.5×) | 1.42 µs (14.8×) | 8.08 µs (79×) | 1.01 µs (10.8×) | 29.31 µs (284×) | 40.51 µs (392×) |
| find-usr | 1.85 µs (1.73×) | 3.30 µs (2.31×) | 10.16 µs (5.03×) | 17.21 µs (7.83×) | n/a (ENOSYS) | 41.53 µs (17.5×) |
| dd-byte-io | 1.27 µs (12.4×) | 1.52 µs (14.6×) | 5.55 µs (50.7×) | 1.05 µs (10.4×) | n/a (ENOSYS) | 31.13 µs (280×) |
| tar-doc | 1.40 µs (1.87×) | 6.19 µs (4.87×) | 14.51 µs (10.1×) | 39.58 µs (25.8×) | n/a (ENOSYS) | 31.66 µs (20.8×) |
| sqlite-100k | (1.03×) | **(21.1×)** | (1.22×) | (1.45×) | (1.04×) | (1.01×) |

**Honest caveats** (from the benchmark author): (1) **sqlite-100k is
compute-bound** (~167 syscalls) → per-syscall numbers are meaningless there; its
signal is that **DBI pays a per-branch cost (21× / 63 s)** while every
syscall-interposer is ~1–1.4×. (2) sqlite native median 2.997 s is ~0.1% under
the 3 s target. (3) **reverie-kvm cannot run find/dd/tar** (ENOSYS on /usr,
/dev/zero, /dev/null after ~50–80 syscalls) — excluded from medians, **not**
counted as wins. (4) one gvisor-systrap `find` sample hung and was replaced
(44.556 s, exit 1); corrected median unchanged (38.281 s, robust).

Marginal `getpid` slope (asymptotic @ N=1M): dbi 1.4 µs · gvisor-kvm 3.7 µs ·
systrap 7.6 µs · reverie-kvm 26 µs · reverie-ptrace 32 µs — consistent with the
amortized ranking.

---

## 5. Lessons & architecture insights

1. **Layer, don't choose.** systrap is **SUD/seccomp fail-closed floor + patching
   fast path**, not SaBRe-*or*-DBI-*or*-SUD. This resolves SaBRe/e9patch's
   fail-open flaw: patching is a pure optimization over a complete trap. A Hermit
   non-ptrace backend should copy this shape.
2. **SUD is the highest-leverage first step** (kernel ≥ 5.11): complete per-thread
   trap, no ptrace, no BPF, cheaper than seccomp. Prototype as a Reverie backend
   next to reverie-ptrace. Hard parts: per-thread setup, alt-stack/sigframe dance,
   clone/exec selector inheritance.
3. **vDSO is mandatory and up-front**, not a follow-up. Ship a replacement vDSO +
   time param page or determinism is a lie for any time-reading program. The DBI
   backend is living proof of the failure mode (`#705`, `date`=1970); the fix is
   to port reverie-ptrace's `vdso.rs` neutralization (plus, for DBI, a DynamoRIO
   code-cache flush).
4. **Steal the transport, not the scheduler.** systrap's shared-memory
   context-queue + dual-spinning is *why* it beats ptrace (no context switch on
   the hot path) and is portable to Rust atomics onto Detcore's transport — but
   its M:N spinning is nondeterministic by design. Keep the transport; drive it
   with Detcore's deterministic scheduler (same seam as the KVM
   `run_with_tool` Guest/Tool adapter).
5. **Keep a ptrace/seccomp-unotify slow path** even in a "ptrace-free" backend for
   awkward syscalls and full reg/FP transfer — systrap itself never fully sheds
   ptrace (`PTRACE_SYSEMU` slow path + reg transfer).
6. **The default is the slow one.** reverie-ptrace, the most-compatible backend,
   is ~40× costlier per syscall than the ~1 µs interposers. This quantifies the
   payoff of maturing DBI/KVM/SaBRe to ptrace-level compatibility, and of an
   SUD-based backend that could plausibly land near systrap's ~8 µs (or better
   with patching) **while staying fail-closed and deterministic** — the
   combination no current Hermit backend achieves.
7. **Fail-open is a correctness bug, not just a coverage gap.** SaBRe's ~1 µs and
   e9patch's low cost are attractive, but a missed syscall site is *silent*
   nondeterminism. systrap's fail-closed floor is the design lesson that makes
   speed safe.

---

*Cross-references: `ai_docs/gvisor-systrap-analysis.md` (systrap internals + source
map); `rrnewton/hermit#705` (DBI clock/vDSO gap); `experiments/gvisor-reverie-benchmark_20260725/`
(raw benchmark data); `PROJECT_VISION.md` (backend roadmap: KVM flagship, DBI, LiteInst LD_PRELOAD).*

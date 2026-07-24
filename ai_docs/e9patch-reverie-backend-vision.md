# e9patch Reverie backend — vision, feasibility, and design

Task `vision-e9patch-backend` (2026-07-24). Research + initial prototype for a
6th Hermit/Reverie backend based on **e9patch** static binary rewriting, as an
alternative/complement to ptrace, DBI (DynamoRIO), KVM, and liteinst.

Evidence for every claim below was gathered on this devserver using the
**vendored** e9patch already in-house at
`~/work/rrn_scx_playground/sched-test/scx-sim/third_party/e9patch` (built
`e9tool` binary present) and the PLDI'20 paper text at
`scratch/liteinst-read-papers/e9patch-pldi20.txt`.

## 1. What e9patch is (and is not)

- **A static, offline binary rewriter.** `e9tool` (frontend) + `e9patch`
  (backend) consume an ELF and emit a *new rewritten ELF* that runs natively.
  It is **not a library** and **not a runtime patcher** — there is no
  `libe9patch` to link; integration is by invoking the `e9tool` CLI (plus
  `e9compile.sh` to compile C instrumentation "trampolines").
- **Control-flow agnostic** (PLDI'20 §2): it treats *every* instruction as a
  potential jump target and preserves the jump-target set, so no CFG recovery
  is needed. Core technique = **instruction punning**: engineer a 5-byte punned
  jump whose bytes overlap the existing instruction so no other instruction has
  to move. Coverage is boosted toward ~100% with **padding** and **eviction**
  (rewrite overlapping instrs to equivalent byte sequences), and space is saved
  with **physical page grouping** (file-backed, shareable executable pages).
  Sites punning can't cover fall back to **int3** trampolines.
- Open source (GPL), github.com/GJDuck/e9patch.

## 2. In-house integration pattern (scx-sim) — the reference to copy

scx-sim (Rust) already ships a working e9patch integration (`--preempt-mode
e9patch`). Pattern (from `crates/scx_simulator/src/preempt/mod.rs`,
`csrc/e9_rbc_trampoline.c`):

1. Write a C trampoline, compile it with `e9compile.sh` (it `#include`s
   e9patch's `stdlib.c`; no libc, no relocations).
2. `e9tool -M <matcher> -P 'call <entry>@<trampoline>' input.so -o output_e9.so`
   produces an instrumented copy.
3. Runtime callback ABI: the trampoline reads a **fixed mmap'd address**
   (`0x1E9000000`) holding a shared struct `{counter, armed, yield_fn}`; the
   Rust host mmaps that struct there *before* loading the `.so`, and the
   trampoline `dlcall`s `yield_fn` (handling 16-byte stack alignment / SSE
   save) to call back into Rust. No dlsym, no RIP-relative, no relocations.

So "call e9patch from Rust" = `std::process::Command` → `e9tool`/`e9compile.sh`,
plus a fixed-address shared page for the trampoline↔Rust callback. This is
proven working in-house.

## 3. Prototype results (this host, vendored e9tool)

- **End-to-end pipeline works.** `e9tool -M 'asm=/ret/' -P print /tmp/e9probe`
  patched 5/8 `ret` sites; the rewritten binary ran correctly (guest stdout
  intact, exit 0) and trampolines fired (3 trap lines). Offline-rewrite →
  native-run → instrumentation-callback all confirmed.
- **Coverage is not automatically 100%**: 5/8 (62.5%) on a tiny function with
  default tactics — real coverage depends on byte values; the paper's near-100%
  needs padding/eviction and is workload-dependent.
- **Nondet instructions live in libc, not the app.** `/tmp/e9probe` (dynamic)
  had **0** `syscall` sites in its own `.text`. `libc.so.6` has **510 syscall
  (100% patchable) + 2 rdtsc**; cpuid/rdtscp/rdrand/rdseed = 0 in this libc
  (they appear in other libs/apps). ⇒ a determinism backend must rewrite
  **every executable object the process maps** (main ELF + ld.so + libc + all
  DSOs), not just the program named on the command line.
- **The vDSO is a hard gap.** `[vdso]` is kernel-mapped with **no on-disk
  file**, so e9patch cannot rewrite it. glibc's `clock_gettime`/`gettimeofday`/
  `getcpu` fast paths run there and never trap → a pure static rewrite cannot
  determinize them. Hermit's ptrace backend already solves this by patching the
  vDSO *in memory at load* (reverie-ptrace/src/vdso.rs). An e9patch backend
  therefore still needs a small runtime component for the vDSO.

## 4. Proposed Reverie backend design

Goal: determinize the nondeterminism-source instructions (SYSCALL, CPUID,
RDTSC/RDTSCP, RDRAND/RDSEED) by **rewriting them offline** into trampolines that
call into Detcore, giving near-native throughput on the hot path (no ptrace
stop, no DBI JIT, no per-syscall trap) for statically-known code.

### Components

1. **Rust offline nondet-site analyzer.** Input: an ELF (main or DSO). Use
   `object` (ELF parsing, sections/segments) + `iced-x86` (linear + targeted
   decode) to enumerate offsets of SYSCALL/CPUID/RDTSC/RDTSCP/RDRAND/RDSEED in
   executable sections. Output: a per-object *nondet-site map*
   `{build-id, path, len, mtime, [ (offset, kind, insn_bytes) ]}`.
   (liteinst2 already has a fail-closed iced-x86 decoder to reuse/mirror.)
2. **Cache** in `~/.cache/hermit/e9map/` keyed by `(path, len, mtime)` (or
   build-id when present). Analyzer is skipped on cache hit. Rewritten `.so`/ELF
   artifacts cached alongside, keyed by the same tuple + trampoline version.
3. **Offline rewrite** with `e9tool` driven by the site map: for each object,
   `-M` the nondet mnemonics (or feed exact offsets via `--use-disasm`) and
   `-P 'call detcore_<kind>@e9_detcore_trampoline'`. The trampoline (compiled
   with `e9compile.sh`) marshals registers and calls into Detcore via a
   fixed-address shared page (scx-sim ABI), where Detcore applies the same
   virtualization it already does in `detcore/src/syscalls/*` and `cpuid.rs`.
4. **Loader.** Launch the rewritten main ELF with `LD_LIBRARY_PATH`/`--library`
   redirection so the rewritten libc/DSOs are used, and mmap the Detcore shared
   page before exec. (e9patch supports rewriting shared libraries; scx-sim does
   exactly this for the scheduler `.so`.)
5. **vDSO + dynamic-code fallback (runtime component).** Patch the vDSO in
   memory at startup (reuse the ptrace-backend approach) so time reads trap.
   For JIT/self-modifying/`dlopen`-after-analysis code the static map misses,
   fall back to signal-based trapping (SIGSEGV/SIGILL on a guard) and/or
   **compose with liteinst LD_PRELOAD** to runtime-patch a missed hot site
   after its first trap — the task's key insight: e9patch can't runtime-patch,
   so a missed hot-loop site pays the signal cost *every* iteration, whereas
   liteinst patches it after the first trap.

### Where it plugs into Reverie/Detcore

- The trampoline→Detcore callback is the analog of `Tool::handle_syscall_event`
  / CPUID/RDTSC hooks. Detcore's determinization logic is **backend-agnostic**
  and already exists; the e9patch backend only needs to deliver events to it and
  read/write guest registers/memory at the trampoline (in-process, like DBI).
- Threading/scheduling: e9patch alone gives no preemption timer. For strict
  multi-thread determinism it would still need an RCB/timer mechanism
  (scx-sim's `_e9.so` counts branches in software via a `-M jcc` trampoline —
  the same trick could provide deterministic preemption without a PMU).

## 5. Comparison with existing backends

| Backend | Mechanism | Hot-path cost | Dynamic/JIT code | vDSO | Coverage of nondet sites |
| --- | --- | --- | --- | --- | --- |
| ptrace | kernel tracer | high (stop/resume per event) | yes | in-mem vDSO patch | full |
| DBI (DynamoRIO) | runtime JIT | medium (JIT + dispatch) | yes | via JIT | full |
| liteinst | runtime in-proc punning | low after 1st trap | yes (runtime) | needs help | patches on demand |
| **e9patch (proposed)** | **offline static punning** | **near-native (direct jump)** | **NO (static only)** | **NO on-disk file → needs runtime help** | **~100% of static sites; misses dynamic + vDSO** |

e9patch's niche: **lowest steady-state overhead for statically-known code**,
paid entirely at (cached) rewrite time. Its structural weaknesses — no runtime
patching, no vDSO, must rewrite every DSO — are exactly what a liteinst/signal
runtime fallback and an in-memory vDSO patch cover.

## 6. Feasibility verdict & recommended first milestone

Feasible and attractive as a *throughput* backend, but it is **not** a drop-in
`--backend e9patch` that satisfies `--strict --verify` on day one, because
Detcore must still be hosted in-process at the trampoline (same integration seam
the DBI backend is blocked on) and the vDSO/dynamic-code fallback is required
for correctness.

Recommended M1 (small, verifiable): a **Rust nondet-site analyzer + cache** (no
Detcore yet) that, for a given ELF and its DSOs, emits the site map and uses
`e9tool -P print` to produce a rewritten binary that **traps on every syscall/
cpuid/rdtsc**, and a test that runs it and confirms the trap fires at the
expected sites. That proves the offline-analysis + rewrite + cache pipeline
end-to-end and de-risks the analyzer, before tackling the (shared-with-DBI)
Detcore-in-process hosting seam.

## 7. Prototyping notes / gotchas

- Use the **vendored** `third_party/e9patch/e9tool` (built) — no need to clone;
  `e9compile.sh` must be run *from* the e9patch dir so `#include "stdlib.c"`
  resolves.
- e9tool rewrites only the file you pass; rewrite libc/ld.so separately and
  redirect the loader to them.
- Trampoline↔Rust ABI: fixed mmap page + `dlcall` for stack alignment (scx-sim
  `e9_rbc_trampoline.c`).
- No repo code was changed for this vision task; prototyping used the in-house
  vendored e9patch read-only plus `/tmp` scratch.

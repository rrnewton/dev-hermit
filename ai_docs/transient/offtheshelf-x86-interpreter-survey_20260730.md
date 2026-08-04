# Off-the-shelf x86 interpreter survey: is there a bulletproof crate for skid-free RIP→next-branch interpretation in a patching backend?

Status: research deliverable for task `research-offtheshelf-x86-interpreter-crate` (2026-07-30).
Author: impl agent (opus-4.8). Companion to
`ai_docs/gvisor-interpret-vs-pmu-skid-preemption_20260730.md`
(task `research-gvisor-interpret-vs-pmuskid`).

## TL;DR / Verdict

**No.** There is no off-the-shelf, *bulletproof*, faithful, permissively-licensed,
in-place-memory x86-64 executor on crates.io (or the wider OSS ecosystem) that
meets the requirement: load CPU regs → faithfully execute straight-line
x86-64 incl. SSE/AVX, mutating **the guest's own** memory in place → write regs
back → resume native.

- **Unicorn Engine** (`unicorn-engine` crate, v2.1.5) — the prime candidate — has
  a near-perfect API fit (`emu_start(begin, until, count)`, full XMM/YMM/ZMM
  register round-trip, even a zero-copy host-memory hatch `mem_map_ptr`) and is
  mature *for its domain* — **but it is disqualified**, with one fatal flaw:
  (1) **FATAL — it cannot execute AVX/AVX2/AVX-512 at all**: Unicorn 2.x rides
  QEMU ~5.0, TCG only got AVX/AVX2 in QEMU 7.2 and *never* AVX-512, so any
  straight-line window with a SIMD instruction (ubiquitous in real binaries:
  `memcpy`/crypto/SIMD) faults; (2) it is **GPLv2** (QEMU fork) vs. our
  **BSD-3-Clause** tree — a copyleft conflict for a shipped linked library;
  (3) its emulation is **best-effort, not bulletproof** (no OS/syscall semantics,
  RIP accuracy only fixed in v2.1.4/Sep-2025, decode bugs #1782/#737, CVEs) — and
  here any divergence is written back to the real thread and silently corrupts it;
  (4) it executes against its **own address space** (the `mem_map_ptr` hatch is
  per-region + unsafe + races other threads, and QEMU's **TB cache** doesn't see
  patched code without a flush — ironic for a patching backend); (5) it **is
  itself a DBT** (QEMU TCG), so adopting it to *avoid* a DBT (DynamoRIO) is
  self-defeating.
- **Decoder/assembler crates** (`iced-x86` — already a dependency here —
  `yaxpeax-x86`, `zydis`) are **decoders, not executors**: useful to *find* the
  next branch and classify instructions, but they cannot run code.
- Rust-native "x86 emulator" crates that exist are **immature / incomplete or
  wrongly licensed**: `mwemu`/`libmwemu` (Apache-2.0, the only permissive,
  actively-maintained pure-Rust executor) is a malware-analysis *simulator*
  (~339 instrs, deliberately non-faithful CPUID, partial/unverified SIMD, not
  bit-faithful); `ax`/`axecutor` is a partial subset, stale, and **AGPL-3.0**.
- The genuinely faithful heavyweights — **Bochs** (LGPL-2.1) and **QEMU/TCG**
  (GPLv2) — are full-system engines with copyleft licenses and their own memory;
  neither is a drop-in in-process register-in/register-out interpreter (extracting
  a userspace CPU core is real engineering). `box64`/`FEX` are ARM64/RV64
  translators; `libx86emu` is 32-bit with no SSE/AVX; `remill` only lifts to LLVM
  IR; faithful formal models (ACL2 `x86isa`, K-x86) are research artifacts.

**Recommendation (matches the owner's decision rule):** do **not** build a
from-scratch interpreter and do **not** adopt Unicorn for production positioning.
**Fall back to DynamoRIO / DBI** for skid-free precise preemption, exactly as
recommended in the gVisor/PMU-skid study. DynamoRIO already runs **in-process,
against real memory, with an inline branch counter** (see
`reverie-dbi/native/client.c`) — which is architecturally the *correct* shape
for this problem and avoids every failure mode above.

---

## 1. The requirement, stated precisely

Skid-free precise preemption = stop the guest thread **exactly** at a target
retired-conditional-branch count (or a target instruction), with no PMU skid and
no long `PTRACE_SINGLESTEP` correction phase.

The task's proposed technique, for a **binary-patching** backend
(e9patch/LiteInst — patch in place, no full DBT code cache): instead of
single-stepping from the current RIP to the next branch, *interpret* that
straight-line stretch:

1. Pull the thread's CPU register state (GP regs, RIP, RFLAGS, and **all** vector
   regs — XMM/YMM/ZMM) into an interpreter.
2. **Faithfully** execute the instructions from RIP up to the next branch,
   **mutating the guest's memory in place**.
3. Write register state back to the CPU.
4. Resume **native** execution at the branch.

The load-bearing words are **faithfully** and **in place**. Because the
interpreter's end-state is written back to a real thread that then continues
natively, the interpreter is not an observer — it *becomes* the execution for
that stretch. Any infidelity is not a bad sample (as in fuzzing); it is
permanent, silent guest corruption.

## 2. Candidate survey

### 2.1 Unicorn Engine (`unicorn-engine` crate) — PRIME CANDIDATE

*What it is:* a CPU-emulator framework — a **fork of QEMU**, built on its **TCG**
dynamic translator — exposing clean register/memory/hook APIs and a
bounded-execution call `uc_emu_start(begin, until, timeout, count)` (Rust:
`emu_start(begin, until, timeout, count)`). You can run "until address X" or
"N instructions", which maps naturally to RIP→branch. The Rust `unicorn-engine`
crate is **first-party** (published by core maintainer wtdcode/lazymio, source in
the Unicorn repo `bindings/rust/`), **v2.1.5 (2025-09-09)**, ~105k downloads,
production-grade *for its domain* (fuzzing / RE / CTF). On paper the API and even
the register/memory plumbing are exactly the shape the task imagines — and yet it
fails five hard requirements (a–e below), one of them fatal by itself.

**(a) FATAL — cannot execute AVX/AVX2/AVX-512 at all.** This is the decisive
finding. Unicorn 2.x rides **QEMU ~5.0**. QEMU-TCG only gained **AVX/AVX2** (plus
F16C/FMA3/VAES) execution in **QEMU 7.2** (Nov 2022), and TCG has **never**
implemented **AVX-512**. So on Unicorn's QEMU-5.0 base, executing an AVX/AVX2/
AVX-512 instruction raises an **illegal/invalid-instruction fault**. Real modern
x86-64 binaries use AVX constantly (glibc `memcpy`/`memset`, crypto, any SIMD),
so *any* straight-line RIP→branch window containing a vector instruction simply
**cannot be executed**. Being able to read/write YMM/ZMM register *state* (which
the binding *does* support, via `reg_read_long`/`reg_write_long`) does **not** mean
it can run the instructions.
  - Refs: QEMU 7.2 TCG-AVX timeline (phoronix.com/news/QEMU-7.2-rc1-Released);
    Unicorn open PR #2143 "bump QEMU to 5.1.0"; Unicorn FAQ ("some instruction
    sets are not implemented by the latest QEMU"). Planned 2.2.0 *may* bump QEMU,
    but as of today AVX is not executable.

**(b) License: GPLv2 (QEMU fork) vs. our BSD-3-Clause.** crates.io metadata is
`"license":"GPL-2.0"` for every version; `COPYING` is verbatim GPLv2. Linking a
GPLv2 library into a shipped hermit/reverie backend (BSD-3-Clause / BSD-2-Clause)
makes the combined work subject to GPLv2 copyleft — no linking exception applies.
Would need legal sign-off, or full process isolation over IPC (itself legally
debated). Acceptable for a throwaway research prototype; **not** for a shipped
backend.

**(c) Memory model — separate address space; a partial in-place escape hatch, but
with a patching-backend trap.** Unicorn executes against its **own** guest
virtual memory (`mem_map` + `mem_write`; unmapped access → `UC_ERR_*`). There
*is* a zero-copy escape hatch — **`mem_map_ptr`** (wraps `uc_mem_map_ptr`) backs a
guest region directly with a **host pointer**, and `mmio_map*` callbacks can proxy
accesses — so "execute against host memory in place" is *partially* achievable,
but only per-region, page-aligned, and `unsafe`. Two problems remain: (1) in a
**multithreaded** guest, other threads mutating shared memory *during* the
emulated window still race the emulator's view → silent corruption (DBI avoids
this — the real memory runs); (2) **TB-cache staleness** — QEMU caches translated
blocks, and edits to already-translated guest code are **not seen** without
`uc_ctl_remove_cache` (FAQ: "Editing an instruction doesn't take effect"). That is
a pointed irony for a **binary-patching** backend whose whole premise is patched
guest code.
  - Refs: binding `mem_map_ptr`/`mmio_map`; FAQ on cache flush and memory-hook
    slowdown; issue #1371 (`UC_ERR_WRITE_UNMAPPED`).

**(d) Faithfulness is best-effort, not bulletproof — and here infidelity is
silent corruption.** Beyond the AVX gap: it is a **pure CPU emulator** with no
syscall/OS semantics (a `syscall` mid-window traps unless hooked); **RIP/PC
accuracy was historically unreliable and only fixed in v2.1.4** (Sep 2025; issues
#1323 RIP-inaccurate-in-mem-hook, #1643 inconsistent regs in block hook) — and
this design writes registers back and resumes native, so PC fidelity is
load-bearing; documented decode bugs (#1782 "Incorrect decoding of REX
prefixes", #737 segment regs, #1440 spurious enumerated registers); multiple CVEs
(CVE-2022-29692/29694/2021-44078). For fuzzing/RE (Unicorn's niche) best-effort is
fine — a wrong flag is a wasted test case. Here the wrong flag/instruction is
written back to a live thread and corrupts the run **nondeterministically and
silently** — the antithesis of a determinism engine's requirements.

**(e) It IS a DBT anyway.** Unicorn = QEMU TCG = a dynamic binary translator.
Pulling in a whole second DBT engine (GPLv2, separate memory, best-effort
fidelity, no AVX) to *avoid* the DBT we already have (DynamoRIO — permissive BSD,
in-process, real memory, inline branch counting) is self-defeating. If the answer
is "a DBT," the right DBT is the one already integrated.

*Verdict on Unicorn:* the API/register/memory plumbing is a near-perfect match,
but it is disqualified for **production** by (a) no AVX execution and (b) GPLv2,
and is architecturally the wrong choice by (c)/(d)/(e). Usable only as a
throwaway research probe, and even then the AVX gap blocks real-world binaries.

### 2.2 Decoder / assembler crates — cannot execute

| Crate | What it is | Execute? | License | Note |
|-------|-----------|----------|---------|------|
| `iced-x86` (icedland/iced) | x86/x64 decoder, encoder, formatter, instruction-info | **No** | MIT | Already a dep in hermit & reverie. Great for *finding*/classifying the next branch; no execution. |
| `yaxpeax-x86` | decoder | **No** | (permissive) | decode only |
| `zydis` / `zydis-rs`, `bddisasm` | disassemblers | **No** | permissive | decode only |
| Intel XED | decoder/encoder | **No** | Apache-2.0 | decode only |

These give instruction boundaries and branch classification — genuinely useful
if one were to *hand-write* an interpreter — but the decision rule forbids
building one, and by themselves they solve none of the execution problem.

### 2.3 Rust-native x86 executors — immature / incomplete / wrong license

| Crate | Executes? | x86-64 + SSE/AVX fidelity | Maturity | License | Verdict |
|-------|-----------|---------------------------|----------|---------|---------|
| `mwemu` / `libmwemu` (was `scemu`) | **Yes** (decodes via iced-x86, interprets) | ~339 instructions; malware-analysis *simulator*; deliberately **fakes CPUID**; Windows-primary, Linux "very basic"; `--banzai` "skip unimplemented" mode; **SSE/AVX coverage unverified/partial** | 312★, actively maintained (pushed 2026-07-30) | **Apache-2.0** (permissive) | Closest permissive pure-Rust executor, but a sandbox simulator, **not bit-faithful**; incomplete coverage |
| `ax` / `axecutor` (`ax-x86`) | **Yes** (subset) | 315 opcodes / 65 mnemonics of ~981; **omits all flags not used by jumps**; "not all instructions behave like real hardware"; no SSE/AVX fidelity | 89★, v0.6.0, **stale (2024-03)** | **AGPL-3.0** (strong copyleft) | Partial + stale + copyleft |
| `rusty_box`, `fotcorn/x86emu`, `d0iasm/x86emu`, `martypc`, `dustbox-rs` | partial/study/16-bit | toys / 8086 / DOS scope | low | mixed | Not viable faithful 64-bit+SIMD executors |

The single permissive, actively-maintained pure-Rust executor (mwemu) is a
malware-analysis process *simulator* with deliberately non-faithful CPU state and
incomplete coverage — the opposite of "bulletproof."

### 2.4 Non-Rust faithful executors via FFI

- **Bochs** (C++): the most genuinely faithful IA-32/x86-64 *interpreter* (not a
  translator), with real SSE/AVX/AVX-512 modeling; very mature, actively
  maintained. **But** it is a whole-machine/PC emulator (BIOS, devices, its own
  memory), **LGPL-2.1**, and *not* a drop-in "run from RIP over host process
  memory, hand registers back" library — extracting just its CPU core is a
  substantial fork. Same in-place-memory problem as Unicorn.
- **QEMU TCG directly**: same engine as Unicorn — GPLv2, dynamic **translator**
  (not a plain interpreter), heavy full-system/user-mode infra, separate memory.
- **box64/box86, FEX-Emu**: dynamic **translators** targeting ARM64/RV64/LoongArch
  hosts (run x86 binaries on non-x86); not faithful in-process interpreters for an
  x86 host. (MIT, very active — but wrong tool.)
- **remill**: only *lifts* machine code to LLVM IR; you'd have to JIT/interpret
  the IR yourself. Not an executor.
- **libx86emu** (SUSE): 32-bit only, README states **no FPU/MMX/SSE/AVX** and no
  64-bit long mode. Unsuitable.

### 2.5 Faithful formal-semantics models (research artifacts, not FFI libraries)

- **ACL2 `x86isa`** (Goel et al., UT Austin): a formally specified, *executable*
  x86-64 ISA model (large user-mode subset incl. many SSE instructions), built
  for faithfulness/proof — but it lives in ACL2/Common Lisp, is research-grade in
  performance/integration, and is not an FFI-callable production library.
- **K-framework x86-64 semantics**: executable formal semantics, same caveat.

None of §2.3–2.5 is a mature, permissive, faithful, in-place-memory, bounded-run
x86-64 executor.

## 3. Why "simulate-ahead" is the wrong shape (and DBI is the right one)

Two families of skid-free positioning:

- **(A) Instrument the REAL execution (DynamoRIO / DBI).** The actual thread runs
  the actual code (via DR's code cache), with an **inline branch counter**, and
  stops itself at the target. Correctness rests on DR's translation fidelity,
  which is battle-tested; memory is the real process memory; other threads see
  real effects. There is no "trust the simulation" gap.
  - See `reverie-dbi/native/client.c` inline `branch_count`; the stop-at-count
    primitive is stubbed today but the primitives exist (`dr_redirect_execution`).

- **(B) Simulate a stretch in a separate interpreter (Unicorn/off-the-shelf).**
  Fails on: no AVX execution (§2.1a — fatal), separate address space
  (§2.1c — copies + multithread races + TB-cache staleness), faithfulness-as-
  correctness (§2.1d — silent write-back corruption), license (§2.1b), and
  being-a-DBT-anyway (§2.1e). Additionally, "RIP → next branch" does
  not cleanly bound the work: a `CALL` into library code is not a conditional
  branch — either you interpret through it (unbounded) or you stop at every
  call/indirect transfer, at which point granularity approaches single-step and
  the win over single-stepping shrinks.

The DBI approach also removes the **PMU hardware dependency** and the fragile
single-step correction phase entirely, and it fixes the current DBI HANGs — see
the gVisor/PMU-skid companion doc.

## 4. Recommendation

1. **Do not adopt an off-the-shelf interpreter for production positioning.** No
   bulletproof candidate exists; Unicorn (the only mature option) is
   GPLv2 + separate-memory + best-effort-fidelity + itself-a-DBT.
2. **Do not build a from-scratch faithful x86-64 interpreter** — per the owner's
   decision rule, that is a large, correctness-critical project reinventing
   DynamoRIO/QEMU.
3. **Use DynamoRIO / DBI** for skid-free precise preemption (in-process, real
   memory, inline branch counting). Remaining work is the same as identified in
   the companion study: safe-point delivery for syscall-free busy-waits (DR
   thread suspension) and moving the scheduler turn out-of-process.
4. **Keep `iced-x86`** for what decoders are good at: locating/classifying the
   next branch, instruction-length decoding for patch sites — it is already a
   dependency and MIT-licensed.
5. *Narrow research-only aside, not recommended for landing:* if one insisted on
   interpretation in a pure patching backend without DR, the only *safe* shape
   would be a bounded hand-rolled interpreter over `iced-x86` covering the common
   integer+SSE subset **with a hard bail-out to single-step on any unrecognized
   or uncertain instruction**. But that is "inventing one" (excluded by the
   decision rule) and its bail path reintroduces single-step. Still: prefer DBI.

## References

(URLs gathered 2026-07-30; read-only research.)

- Unicorn Engine: github.com/unicorn-engine/unicorn — GPLv2 (README + COPYING),
  QEMU fork; Rust binding `unicorn-engine` v2.1.5 (crates.io API:
  `"license":"GPL-2.0"`, published by wtdcode), source `bindings/rust/`.
- QEMU-TCG AVX timeline: phoronix.com/news/QEMU-7.2-rc1-Released (AVX/AVX2 landed
  in QEMU 7.2; AVX-512 never in TCG); Unicorn PR #2143 (bump QEMU to 5.1.0),
  Unicorn `ChangeLog` (2.x on QEMU ~5.0), FAQ (unimplemented instruction sets,
  cache-flush, memory-hook slowdown).
- Unicorn issues: #1782 (REX-prefix decode), #1323 (x86_64 RIP inaccuracy in mem
  hook), #1643 (inconsistent regs in block hook), #737 (segment register
  ignored), #1440 (nonexistent enumerated registers), #1371 (UC_ERR_WRITE_UNMAPPED).
- Unicorn CVEs: CVE-2022-29692, CVE-2022-29694, CVE-2021-44078 (opencve.io).
- AFL++ unicorn mode: github.com/AFLplusplus/unicornafl (Unicorn's real niche).
- iced-x86: github.com/icedland/iced (MIT) — "disassembler, assembler, decoder,
  encoder" + instruction-info API (no execution).
- Other decoders: github.com/iximeow/yaxpeax-x86 (SPDX unverified),
  github.com/zyantific/zydis (MIT), github.com/bitdefender/bddisasm, Intel XED.
- Rust executors: github.com/mwemuorg/mwemu (Apache-2.0, simulator),
  github.com/xarantolus/ax (AGPL-3.0, partial/stale).
- Faithful heavyweights: github.com/bochs-emu/Bochs (LGPL-2.1),
  QEMU/TCG (GPLv2). Translators (wrong tool): github.com/ptitSeb/box64,
  github.com/FEX-Emu/FEX. Lifter: github.com/lifting-bits/remill.
  32-bit only: github.com/wfeldt/libx86emu. Formal: ACL2 x86isa, K-x86.
- DynamoRIO: github.com/DynamoRIO/dynamorio (BSD) — in-process DBI, code cache.
- Companion: `ai_docs/gvisor-interpret-vs-pmu-skid-preemption_20260730.md`.
- reverie DBI inline branch counter: `reverie-dbi/native/client.c`.

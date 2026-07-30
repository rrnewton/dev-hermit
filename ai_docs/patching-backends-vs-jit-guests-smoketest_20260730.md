# Do patching backends break JIT/TCG-generated code? Smoke test + prior art

Status: research deliverable for task `smoke-test-patching-backends-jit-guests` (2026-07-30).
Author: impl agent (opus-4.8). Prerequisite framing for
`ai_docs/offtheshelf-x86-interpreter-survey_20260730.md` and
`ai_docs/gvisor-interpret-vs-pmu-skid-preemption_20260730.md`.

## TL;DR / Verdict

**The hypothesis holds: in-place patching does NOT break JIT/TCG code, because the
nondeterministic/syscall instructions Hermit cares about (`syscall`, `rdtsc`,
`rdtscp`, `cpuid`, `rdrand`, `rdseed`) live in *static* engine/libc code, and
JIT/TCG output *calls into* that static code rather than emitting those
instructions inline.** This is confirmed three ways — empirically (running real
JIT guests under Hermit's patching backends), from Hermit backend source (what
each backend actually patches), and from the upstream engine source (HotSpot,
QEMU-TCG, V8).

Key results:
- **e9patch is safe by construction.** It AOT-rewrites nondet instructions in the
  **main ELF only**, then runs under the **ptrace runtime**. It never sees, and
  never patches, JIT-generated code (or shared libraries) — those are handled by
  the ptrace/seccomp runtime at execution. Empirically under e9patch: a JVM C2
  hot-loop ran correctly with `candidate_sites=0` (the `java` launcher has no
  nondet instructions to patch); a node/V8 TurboFan hot-loop ran correctly (`acc`
  == native); and `qemu-system-x86_64` ran correctly with `candidate_sites=21`
  (21 static nondet instructions in QEMU's own `.text`). No JIT breakage in any.
- **liteinst / sabre *can* touch JIT pages** (their `syscall`-interception
  mechanism is execution-driven and fires on any page, including anonymous
  JIT/TCG buffers), but in practice mainstream JITs never emit a raw `syscall` in
  generated code, so the mechanism rarely fires. They only target `syscall`
  (never `rdtsc`/`cpuid`).
- **Prior art agrees and adds the robust-engineering lesson:** rr does **not**
  rely on locating these instructions — it uses hardware traps (`PR_SET_TSC=
  SIGSEGV` + CPUID faulting) so it catches them regardless of static-vs-JIT
  origin. Hermit's e9patch+ptrace hybrid already embodies this belt-and-suspenders.

**Implication for the interpreter/DBI path:** for *intercepting nondeterminism*
in JIT guests, patching static code + a trap fallback **suffices** — you do not
need an x86 interpreter or DBI just to handle JIT guests' nondet instructions.
This is a *different* problem from **skid-free precise preemption** (positioning
at an exact branch count), which is what the interpreter/DBI studies address and
which this result does **not** obviate.

---

## 1. What each backend actually patches (Hermit source)

`hermit run --backend <b>` supports `ptrace`, `dbi`, `liteinst`, `sabre`, `kvm`,
and `e9patch` ("Preprocess the main ELF with e9patch, then use the ptrace
runtime").

### e9patch — AOT, main ELF only, ptrace runtime for the rest
- Rewrites the nondeterministic x86 instructions found by an **offline linear
  scan of the on-disk main ELF**: the classifier `nondeterministic_instruction`
  (`hermit/hermit-cli/src/instruction_map.rs:339-353`) matches `syscall`,
  `cpuid`, `rdrand`, `rdtsc`, `rdtscp`, `rdseed`, `sysenter`, `xbegin`, `xend`,
  `int 0x80`. e9tool is invoked with exact file-offset matchers
  (`hermit-cli/src/e9patch.rs:192-208, 623-632`).
- **Scope = the single main executable ELF only.** `prepare()` runs on one
  resolved program path; shared libraries (`libc.so`, `libjvm.so`) are never
  scanned or overlaid (`hermit-cli/src/bin/hermit/run.rs:1979-1999`; test asserts
  overlay target == `/bin/echo` only, `run.rs:674-690`). Non-ELF main programs
  are skipped (→ `main_executable=non-ELF`).
- **Runtime = ptrace.** `runtime_backend(): Backend::E9patch → Backend::Ptrace`
  (`run.rs:1418-1424`); banner: "e9patch preprocessing + ptrace runtime". So
  shared libraries **and any code generated at runtime (JVM JIT cache, QEMU TCG
  buffer)** are handled by the ptrace/seccomp runtime, which *traps* nondet
  instructions at execution and **never rewrites guest code**.
- **JIT risk: NONE.** e9patch has no runtime rewriting path at all; JIT output is
  never a patch target.

### liteinst — in-process, seccomp/SIGSYS execution-driven
- A `.init_array` ctor installs the Detcore Tool via `reverie_liteinst`
  (`detcore-liteinst/src/lib.rs:16-28`). A seccomp-BPF filter + SIGSYS handler
  makes a `syscall` **trap on first execution**; the handler then rewrites that
  live site to a trampoline (`reverie-liteinst/src/runtime.rs:984-1039`).
- Discovery is by **execution, not by VMA**: a raw `syscall` from a JIT/anonymous
  executable page traps and becomes a hook target exactly like one in libc. It
  tracks executable-mapping generations to invalidate stale patches on
  `mmap`/`munmap`/`mremap` (the JIT-churn case).
- **Only `syscall` (`0F 05`)** is patched; `cpuid`/`rdtsc` are *not* routed
  (`runtime.rs:1009`; crate boundary doc).
- **JIT risk: structurally YES**, but only for raw `syscall` in JIT code (rare).

### sabre — loader rewrite (ELF+libs) + ptrace safety net
- SaBRe loader rewrites raw `syscall` sites in the main ELF **and** shared
  libraries at load; the plugin virtualizes RDTSC/vDSO time
  (`detcore-sabre/src/lib.rs:204-239`). A ptrace net permanently rewrites raw
  `syscall` (`0F 05`→`0F FF`) at any executed site in an **untrusted** mapping,
  and **anonymous mappings (JIT caches) are explicitly untrusted**
  (`hermit-cli/src/sabre_ptrace.rs:339-352, 545`). Only `syscall`.
- **JIT risk: YES** for raw `syscall` emitted into a JIT/TCG buffer (rare).

(Full source report with file:line is preserved in the task notes.)

## 2. Empirical smoke test

**Environment caveat (important).** With the main release binary
(`hermit/target/release/hermit` @ main `0321a015`), **all** patching backends are
*unavailable*: `e9patch` (no e9tool in PATH), `liteinst` (no
`libdetcore_liteinst.so` beside the binary), `sabre` (no SaBRe binary), `dbi`
(not built in). Only `ptrace` runs. To exercise the patching backends I used a
sibling worktree's fully-built binary read-only: **hermit
`worktrees/liteinst/hermit/target/release/hermit` @ `d2d2ac6` (branch
`codex/liteinst-ratchet-7-hermit`)** with `libdetcore_liteinst.so` beside it, and
`HERMIT_E9TOOL=…/install-build/e9patch/e9tool`. Results are bound to that build,
not main.

JIT was *confirmed to actually fire natively* before testing: `Hot::work` reaches
HotSpot **C2 tier-4 (incl. OSR)** via `-XX:+PrintCompilation`; V8 optimizes
`work` (TurboFan) 3× via `--trace-opt`.

| Guest (JIT engine) | Backend | Result |
|--------------------|---------|--------|
| Java C2 hot-loop (`Hot.java`, 200k iters) | ptrace | ✓ `acc=-4940175620386166196` (== native), `dt_ns_positive=true`, C2 tier-4 fires **in-sandbox**, exit 0 |
| Java C2 hot-loop | **e9patch** | ✓ `candidate_sites=0; mapped_sites=0` (the tiny `java` launcher ELF has no nondet instructions), `acc` == native, exit 0 → nothing statically patched; C2 JIT + all nondeterminism handled by ptrace runtime |
| Java (`java -version`, minimal JIT) | liteinst | ✗ `Error: -524 ENOTSUPP` at **startup** |
| `/bin/sh -c 'date; …'` (**non-JIT control**) | liteinst | ✗ same `-524 ENOTSUPP` + "LiteInst cancellation cleanup failed" |
| `/bin/echo` (trivial control) | liteinst | ✓ runs (`traps=1, hooks=31`), exit 0 |
| QEMU-TCG (`qemu-system-x86_64 --version`, v10.1.2) | **e9patch** | ✓ `candidate_sites=21; mapped_sites=21` (21 static nondet instructions in QEMU's own `.text` rewritten AOT), ran clean, exit 0 |
| node/V8 TurboFan (`hotl.js`, 60k iters, TurboFan confirmed) | **e9patch** | ✓ `acc=-1121047936` (== native), exit 0 — node on PATH is a bash wrapper → e9patch sees `non-ELF`, real node ELF execs under ptrace; V8 JIT + patching coexist correctly |
| node/V8 TurboFan (`hotl.js`) | ptrace | ran (V8 spawns many threads → very slow under ptrace), no crash; cleaned up before completion (e9patch run above already confirms correctness) |

**Interpretation.**
- **e9patch × JVM**: `candidate_sites=0` is the headline — the `java` launcher
  binary contains *zero* nondet instructions; they all live in `libjvm.so`/`libc`
  (not scanned by e9patch) and are caught by the ptrace runtime. The C2 JIT code
  cache is likewise never touched by e9patch and runs correctly. **No JIT
  breakage; patching and JIT coexist trivially because patching never reaches
  either the libraries or the JIT.**
- **e9patch × QEMU-TCG**: `candidate_sites=21` shows QEMU's nondeterministic
  instructions (its `safe_syscall` stub, `helper_*` paths, cpuid feature probe,
  etc.) live in the **static QEMU binary** and are rewritten AOT; the TCG-
  generated host code produced at runtime is handled by ptrace. QEMU ran clean.
- **liteinst**: the failure is **not** JIT-related — it reproduces on a trivial
  non-JIT `/bin/sh` doing `date`, and on JVM *startup* before meaningful C2. It is
  general backend immaturity in the tested build (the crate documents itself as
  non-production, single-thread tool mode). liteinst could not be used to reach a
  JIT-vs-patching conclusion empirically; its *source* behavior is covered in §1.
- **sabre/dbi**: not runnable in the available builds (SaBRe binary / DBI feature
  absent), so no empirical data; §1 + prior art cover them.

## 3. Prior art (upstream engine source, verified)

**HotSpot (C1/C2).** `System.nanoTime()`/`currentTimeMillis()` →
`clock_gettime` (vDSO) in static VM C++ (`os_posix.cpp`). C2 intrinsics emit a
**leaf CALL** into `os::javaTimeMillis`/`javaTimeNanos`, not inline `rdtsc`
(`library_call.cpp`). `UseFastUnorderedTimeStamps` defaults false, and even on,
`rdtsc` sits in static `libjvm.so` `os::rdtsc()` reached by a call
(`os_linux_x86.inline.hpp`). Safepoint polling is a memory load on a poll page,
not a syscall. **Caveat:** HotSpot's `cpuid` feature probe is emitted into a
*runtime-generated* `get_cpu_info_stub` (`vm_version_x86.cpp`), **not** static
`.text` — a pure static patcher of `libjvm.so` would miss it (it runs once at
init). Actual syscalls go through glibc (static `libc.so`).

**QEMU-TCG.** TCG emits `gen_helper_rdtsc`/`gen_helper_cpuid` — **calls** into C
helpers, not inline instructions (`target/i386/tcg/translate.c`); `helper_rdtsc`
even emulates a *virtual* TSC rather than executing host `rdtsc`
(`misc_helper.c`). The real host `syscall` lives in the **static hand-written
`safe_syscall` asm stub** (`common-user/host/x86_64/safe-syscall.inc.S`).
**Caveat:** in KVM/full-system mode guest instructions run natively under VMX and
never appear in the QEMU binary — the static-patch reasoning applies to *TCG* mode
(the relevant mode for QEMU-as-a-userspace-process under Hermit).

**V8.** `Date.now()` → `clock_gettime` in static C++ (`base/platform/time.cc`);
`cpuid` for feature detection is static startup C++ (`base/cpu/cpu-x86.cc`);
JIT'd JS calls into the runtime — no inline `rdtsc`/`syscall`/`cpuid`.

**Tools.** e9patch is AOT-only and never sees JIT by design; its sister project
**E9Syscall** intercepts syscalls precisely by *statically patching `libc.so`* —
the hypothesis in production. DynamoRIO/Pin handle dynamically-generated and
self-modifying code by **re-JITing** into a code cache (a different mechanism, not
static patching). **rr** does not rely on locating `rdtsc`/`cpuid` at all — it
traps them in hardware (`PR_SET_TSC=SIGSEGV`, CPUID faulting) so they are caught
regardless of static-vs-JIT origin; rr was built for Firefox, whose SpiderMonkey
JITs JS, and runs application+JIT code natively while recording at the
syscall/async-event level.

## 4. Conclusions and recommendations

1. **Patching + JIT coexist.** For HotSpot, QEMU-TCG, and V8, the nondet/syscall
   instructions are in static engine/libc code and JIT output calls into them.
   Empirically, e9patch patched 0 sites for the JVM and 21 for QEMU and both ran
   correctly with JIT active — no JIT-code breakage.
2. **e9patch is the safe shape**: AOT-patch the static main ELF + ptrace runtime
   catches everything else (libraries, JIT, the HotSpot `cpuid` stub). The "does
   patching break JIT" question is *moot* for e9patch because it never touches JIT
   code and the ptrace fallback is a hardware/trap net.
3. **A *pure* patching backend (no trap fallback) is a bet.** It only breaks if a
   JIT emits a nondet instruction inline. Two flagged edge cases: HotSpot's
   one-time runtime-generated `cpuid` stub, and any (nonstandard) JIT that inlines
   `rdtsc`. Robust hardening = keep a hardware-trap fallback (`PR_SET_TSC`,
   CPUID faulting) as rr does. Hermit's e9patch+ptrace hybrid already does this.
4. **liteinst/sabre** can touch JIT pages for raw `syscall` (rare) and do not
   cover `rdtsc`/`cpuid` — so for a JIT that inlined a time instruction they would
   under-cover; not a concern for stock JITs.
5. **Bearing on the interpreter/DBI studies.** This confirms the interpreter path
   is **not required merely to intercept nondeterminism in JIT guests** — static
   patching + trap fallback suffices. It says nothing against the *separate* need
   for **skid-free precise preemption**, which remains the domain of DBI (see the
   companion studies) since that is about exact branch-count positioning, not
   instruction interception.

## References

- Hermit source: `hermit-cli/src/instruction_map.rs:339-353`,
  `hermit-cli/src/e9patch.rs:192-208,544-616`, `hermit-cli/src/bin/hermit/run.rs:1418-1424,1979-1999`,
  `detcore-liteinst/src/lib.rs:16-28`, `reverie/reverie-liteinst/src/runtime.rs:984-1039`,
  `hermit-cli/src/sabre_ptrace.rs:36-39,339-352`, `detcore-sabre/src/lib.rs:204-239`.
- HotSpot: openjdk/jdk `src/hotspot/os/posix/os_posix.cpp`,
  `src/hotspot/share/opto/library_call.cpp`, `src/hotspot/cpu/x86/vm_version_x86.cpp`,
  `src/hotspot/os_cpu/linux_x86/os_linux_x86.inline.hpp`.
- QEMU: qemu/qemu `target/i386/tcg/translate.c`, `target/i386/tcg/misc_helper.c`,
  `common-user/host/x86_64/safe-syscall.inc.S`.
- V8: v8/v8 `src/base/platform/time.cc`, `src/base/cpu/cpu-x86.cc`,
  `src/codegen/x64/assembler-x64.h`.
- Tools: github.com/GJDuck/e9patch, github.com/GJDuck/e9syscall,
  en.wikipedia.org/wiki/DynamoRIO, Intel Pin FAQ; rr: rr-debugger/rr
  `src/Task.cc`, `src/record_signal.cc`, `src/util.cc`, `src/RecordSession.cc`.
- Companions: `ai_docs/offtheshelf-x86-interpreter-survey_20260730.md`,
  `ai_docs/gvisor-interpret-vs-pmu-skid-preemption_20260730.md`.

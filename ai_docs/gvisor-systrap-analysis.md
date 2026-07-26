# gVisor `systrap` platform — architecture study and lessons for a hermit/reverie non-ptrace backend

Author: hermit-170 (impl/research agent, opus-4.8)
Task: `research-gvisor-systrap`
Date: 2026-07-25
Source: upstream gVisor checkout at `experiments/gvisor/` (commit `8eb8f9e0d`),
`pkg/sentry/platform/systrap/` and `pkg/sentry/platform/systrap/sysmsg/`.

> TL;DR — systrap is the production answer to *exactly* the problem the hermit-v2
> roadmap P1 gate is deciding (SaBRe vs DynamoRIO vs "custom SUD/patching"):
> intercept guest syscalls with low overhead, no per-syscall ptrace, and no host
> root. It does it with a **layered** mechanism — **Syscall User Dispatch (SUD)
> as the primary trap, seccomp `SECCOMP_RET_TRAP` as the pre-5.11 fallback, and
> in-place binary patching of `mov;syscall` sites as the fast path** — glued to a
> **shared-memory context queue with dual (sentry+stub) spinning** so the hot
> path executes with zero syscalls when a CPU is free. It replaced ptrace as
> gVisor's default in mid-2023. It still needs **CAP_SYS_PTRACE** (satisfied
> inside an unprivileged user namespace) for stub bootstrap, full register/FP
> transfer, and a slow-path syscall executor.

---

## 1. How systrap intercepts syscalls (three layers, not one)

The README says "seccomp `SECCOMP_RET_TRAP` → SIGSYS", but the current code is
richer. There are **three** cooperating layers:

### (a) Primary trap: Syscall User Dispatch (SUD), kernel ≥ 5.11
`subprocess.go:288-305` enables SUD on every stub thread:
```go
// Enable syscall user dispatch for trapping guest system calls.
// This mechanism, introduced in kernel 5.11, is generally more
// efficient than seccomp. On old kernels, syscalls will be trapped by seccomp.
t.syscallIgnoreInterrupt(&t.initRegs, unix.SYS_PRCTL,
    PR_SET_SYSCALL_USER_DISPATCH, PR_SYS_DISPATCH_ON,
    stubStart, stubROMapEnd-stubStart, 0)   // [selector range = stub code]
```
SUD flips a per-thread mode: any `syscall` instruction executed **outside** the
allow-listed PC range `[stubStart, stubROMapEnd)` (the stub's own code) delivers
`SIGSYS` synchronously. Guest code is always outside that range, so every guest
syscall traps; the stub's own syscalls (futex, mmap, rt_sigreturn…) don't. No
BPF program is evaluated per syscall — cheaper than seccomp.

### (b) Fallback trap: seccomp `SECCOMP_RET_TRAP`, kernel < 5.11
If `PR_SET_SYSCALL_USER_DISPATCH` returns `EINVAL`, the stub falls back to the
BPF filter built in `filters.go` + `subprocess_linux.go`. Default action for
guest syscalls is `seccomp.Trap` → `SIGSYS`. Same signal, same handler; only the
trap source differs. (This is the mechanism the README describes.)

### (c) Fast path: in-place binary patching (`usertrap/`)
The `SIGSYS` handler (`sysmsg/sighandler_amd64.c:251-317`) inspects the 7 bytes
at the faulting RIP. If it sees the canonical glibc pattern
`b8 <sysno:4>  0f 05` = `mov $sysno,%eax ; syscall`, it flags the context
`CONTEXT_STATE_SYSCALL_NEED_TRAP`. Back in the sentry,
`usertrap_amd64.go:PatchSyscall` rewrites those 7 bytes in the guest's memory to
`ff 24 25 <trapAddr:4>` = `jmp *trapAddr`, pointing at a per-syscall trampoline
in a `[usertrap]` table mapped at `0x60000`. The trampoline (built for
`__export_syshandler`, `syshandler_amd64.S`) saves state to the shared
`thread_context` and calls the handler **as a plain function — no signal is ever
raised again** for that site. This is the decisive win over both ptrace and
plain seccomp/SIGSYS.

Patching subtleties worth stealing:
- The patch is applied in **3 non-atomic steps** designed so any concurrent
  thread reads a self-consistent instruction stream at every instant. Step 1
  overwrites the first syscall byte with the 1-byte invalid opcode `0x06`; any
  thread mid-decode faults there and is restarted (`HandleFault`,
  `usertrap_amd64.go:304`). The `[usertrap]` table is mapped at `0x60000`
  precisely because its high byte `0x06` is itself an invalid opcode, so a
  torn read still faults at the same spot.
- Patching is **disabled while a task is ptraced** (`PatchSyscall:196-212`) —
  single-stepping is incompatible with the `%gs`-based syshandler. Toggle:
  `--systrap-disable-syscall-patching`. Also breaks if the guest itself uses
  `swapgs` or sets the TF flag (`syshandler_amd64.S` header comment).

---

## 2. Does it need root? — No host root, but **yes CAP_SYS_PTRACE (in a userns)**

The task premise ("without root") is only half right, and the nuance matters for us.

- `systrap.go:391`: `Requirements{RequiresCapSysPtrace: true}`.
  `runsc/cmd/capability_test.go:123`: *"CAP_SYS_PTRACE … added due to the platform choice."*
- ptrace is **not** on the per-syscall hot path. It is used for:
  stub bootstrap (`PTRACE_ATTACH`, `wait`, `grabInitRegs`);
  full register + FP state transfer (`PTRACE_GETREGSET`/`SETREGSET`,
  `PTRACE_GETSIGINFO`, `systrap_unsafe.go`); and a **slow-path syscall
  executor** using `PTRACE_SYSEMU` (`handlePtraceSyscallRequest`,
  `subprocess.go:238`) for syscalls the shared-memory syscall-thread can't run
  safely (see §5).
- "Without root" = **unprivileged user namespaces**. `--rootless`
  (`runsc/config/flags.go:113`) does `CLONE_NEWUSER`
  (`specutils/namespace.go:47`); inside that namespace the process is uid 0 and
  holds CAP_SYS_PTRACE over its own descendants. Needs
  `/proc/sys/kernel/unprivileged_userns_clone=1` (`hostsettings.go:275`).

**Implication for us:** a hermit/reverie "SUD + patching" backend modelled on
systrap would *also* want CAP_SYS_PTRACE for bootstrap + full-state transfer +
slow-path syscalls, obtainable via an unprivileged userns. A **truly**
zero-capability backend must give up ptrace entirely and do all register
transfer through the signal frame / SUD selector + shared memory — which is
harder but not impossible (systrap already reads/writes most state through the
shared sigframe; ptrace is the residual).

---

## 3. vDSO — the mandatory piece everyone forgets

vDSO time calls (`clock_gettime`, `gettimeofday`, `getcpu`, `time`) execute in
userspace **without a `syscall` instruction**, so **no** interception mechanism
(SUD, seccomp, or ptrace) traps them. systrap's answer:

- gVisor **ships its own vDSO** compiled into the sentry (`vdso/vdso.cc`,
  `vdso/vdso_time.cc`) and maps it into the guest at load time
  (`pkg/sentry/loader/vdso.go`), replacing the host vDSO.
- That custom vDSO reads time from a **shared parameter page** the sentry
  maintains (`kernel/vdso.go: VDSOParamPage`) via a seqlock — a pure userspace
  read, fully sentry-controlled, no trap (`vdso_time.cc:32` struct params).
- When the param page marks a clock not-ready/unsupported it **falls back to a
  real `sys_clock_gettime`** (`vdso_time.cc:116,149`) which then traps normally.
- Legacy vsyscall (fixed `0xffffffffff600000`) is trapped by a dedicated seccomp
  `Vsyscall` rule and re-presented to the sentry as `SIGSEGV`
  (`maybePatchSignalInfo`, `subprocess_amd64.go:151`).

**Lesson (cross-ref `sabre-determinism-analysis.md:82`, MEMORY
`clock-monotonic-already-deterministic`):** any non-ptrace hermit backend that
claims to virtualize/determinize time **must** replace or neutralize the guest
vDSO. hermit's ptrace path already virtualizes vDSO; a SUD/patching path would
inherit this requirement wholesale. This is a first-class deliverable, not a
follow-up. SaBRe today routes only 4 vDSO functions and lets "alternate
code/mappings escape" — systrap's whole-vDSO-replacement is the more robust
model.

---

## 4. fork / exec / signals

Handled almost entirely by the **sentry (userspace kernel)**, not the platform.
The systrap platform surface is only `NewAddressSpace / NewContext / Switch /
MapFile`.

- **fork/clone/execve** are ordinary trapped guest syscalls. The sentry
  implements semantics and calls the platform: new mm → `NewAddressSpace` → a
  new forked stub **process** (`systrap.go:363`); new thread → new
  `thread_context` + a sysmsg worker thread; execve → rebuild address-space
  mappings via `MapFile` into the stub.
- Stub process tree is created with `clone(CLONE_FILES|CLONE_PARENT|SIGCHLD)`;
  sysmsg threads with `clone(CLONE_VM|CLONE_FS|CLONE_FILES|CLONE_PTRACE|SIGKILL)`
  (`subprocess_linux.go:59-69`). `PDEATHSIG=SIGKILL` + parent-death chaining
  ensures OOM/kill of any stub tears down the whole tree
  (`createStub` comment: *"not possible to safely handle a single stub getting
  killed"*).
- **Guest signal delivery** is *synthesized by the sentry* by editing the
  guest's register/stack (building a signal frame in guest memory) — the host
  never delivers guest-destined signals. The 6 host signals the stub installs
  (`SIGSYS/SIGSEGV/SIGBUS/SIGFPE/SIGTRAP/SIGILL`) are purely the trap +
  hardware-fault mechanism (README:21-22).
- **`SIGCHLD` is repurposed as the sentry→stub preempt/interrupt kick**
  (`tgkill`); the handler postpones it while inside the syshandler fast path
  (`sighandler_amd64.c:223`).
- **Faulting on possibly-unmapped guest memory** from inside the handler is made
  safe by a `fault_jump` trick: set the expected fault size, do the read; if it
  faults, the nested handler bumps RIP past it (`sighandler_amd64.c:228`,
  `sysmsg.h:__panic`). Useful pattern for any in-guest control code.

---

## 5. Performance model — decoupling + dual spinning

The real architectural innovation is **decoupling three things** that ptrace
fuses into a rigid 1-tracer:1-tracee relationship:

| Object | = | Lives in |
|---|---|---|
| **AddressSpace** | one stub **process** (own page tables) | host process |
| **thread_context** | one guest thread's register+FP state | **shared memory** (`sysmsg.ThreadContext`) |
| **sysmsg thread** | a host worker that runs contexts | host thread in the stub process |

M guest contexts are multiplexed over N worker threads through a **shared-memory
ring buffer** (`context_queue.go` / `sysmsg_lib.c: struct context_queue`).

**Hot path has zero syscalls when a CPU is free:**
1. Sentry (`switchToApp`, `subprocess.go:813`) writes guest regs into the shared
   `thread_context`, enqueues it, and — on the fast path — **spins**
   (`dispatcher.waitFor`, `sentry_fast_path`) waiting for the state to change.
2. A stub worker **spins** in `get_context_fast` (`sysmsg_lib.c:250`) via a
   lock-free `spinning_queue`, pops the context, restores it onto its signal
   frame, and returns to guest.
3. Guest syscall → (patched) `jmp` into `__export_syshandler` (no signal) or
   `SIGSYS` → handler writes result to shared mem, sets context state, and the
   spinning sentry sees it immediately.
4. Only after `deep_sleep_timeout` of idle spinning does either side fall back to
   `FUTEX_WAIT/WAKE` (`switch_context`/`get_context`). A `controlFastPath`
   goroutine (`systrap.go:334`) globally toggles spinning based on load / CPU
   count (disabled entirely if only 1 CPU).

Cost comparison (qualitative; g3doc `performance.md`/`platforms.md`):
- **ptrace**: ≥4 host context switches per guest syscall (enter-stop, sentry,
  exit-stop, cont). High, syscall-bound workloads pay heavily.
- **systrap**: 1 signal (unpatched) or 1 function call (patched) + a
  shared-memory handshake that is often just a spin, no context switch.
- **KVM**: lowest interception cost on bare metal, but nested-VM overhead makes
  systrap win inside VMs. systrap became the default in mid-2023.

---

## 6. Comparison to our in-repo backend efforts

Correction from repo survey: there is **no `reverie-preload` crate**. The
`shmem_exec_obj/pod-*` dirs are a *shared-code-object* POC (LD_PRELOAD used only
to interpose 4 libc credential functions + shared-memory counters); it is **not**
a syscall-interception backend and does not trap by syscall number. The relevant
non-ptrace backend designs live in `ai_docs/`.

| Aspect | gVisor **systrap** | reverie **ptrace** (baseline) | **SaBRe** (`ai_docs/sabre-determinism-analysis.md`) | **e9patch** (`ai_docs/e9patch-*`) | **KVM/Sentry** (`PROJECT_VISION.md`) |
|---|---|---|---|---|---|
| Trap mechanism | SUD (→SIGSYS) + seccomp fallback + `mov;syscall`→`jmp` patch | seccomp `RET_TRACE` + ptrace stops | in-process ELF instruction rewriting | static offline rewriting + guard-fault | ring3→ring0 `SYSCALL` fault to Sentry |
| Coverage of raw/JIT/static syscalls | **complete** (SUD/seccomp by whole-thread, not by site) | complete | **fail-open** — misses DSO/JIT/static | fail-open until first trap | complete |
| Monitor | in-process Go Sentry | out-of-proc tracer | in-process plugin | in-process | Rust tool over KVM |
| Sentry↔worker sync | shared-mem ring + dual spin + futex | ptrace stop/cont | direct call | direct call | vmenter/vmexit |
| vDSO | **own vDSO + param page** | virtualized | 4 fns routed, rest escape | unrewritable, escapes | Sentry-provided |
| Root | no host root; **CAP_SYS_PTRACE in userns** | CAP_SYS_PTRACE | none | none | `/dev/kvm` |
| Maturity | **production default** | production | experimental draft | vision | proposal |

The critical axis is **coverage**. SaBRe/e9patch are *fail-open*: a syscall in an
undiscovered mapping (dlopen'd DSO, JIT, static binary, hand-written asm) runs
un-intercepted and silently breaks determinism
(`sabre-determinism-analysis.md:80,113-115`). systrap is *fail-closed*: SUD /
seccomp are per-thread properties, so **every** `syscall` instruction traps
regardless of which mapping it's in; patching is only an optimization layered on
top of a complete trap. That is the property `sabre-determinism-analysis.md:340`
explicitly recommends adding ("combine rewriting with Syscall User
Dispatch/seccomp fallback"), and it is the property `hermit-v2-roadmap.md:242`
calls out SUD as the strongest primitive for.

---

## 7. Actionable insights for hermit/reverie

1. **Adopt the layered model for a custom fast backend, don't pick one
   mechanism.** systrap proves the winning shape for the roadmap P1 gate is not
   "SaBRe *or* DynamoRIO *or* SUD" but **SUD/seccomp as the fail-closed trap
   floor + instruction patching as the opportunistic fast path.** Patching gives
   the speed; SUD/seccomp guarantees correctness/determinism when a site isn't
   (yet) patched. This directly resolves SaBRe's fail-open weakness.

2. **SUD is the highest-leverage first step and is cheaper than seccomp.** A
   `PR_SET_SYSCALL_USER_DISPATCH` selector over the guest ranges gives complete,
   per-thread, syscall-number-agnostic interception on any kernel ≥5.11 with **no
   ptrace and no BPF**. Prototype this in reverie as a `Backend` alongside
   `reverie-ptrace`. Hard parts (documented at `hermit-v2-roadmap.md:243`):
   per-thread setup, the alt-signal-stack/sigframe dance, and clone/exec
   inheritance of the selector.

3. **Budget the vDSO work up front.** Whichever fast backend wins, it must ship
   a replacement/neutralized vDSO + a sentry-maintained time param page, exactly
   like `vdso/vdso_time.cc` + `VDSOParamPage`. Reuse hermit's existing vDSO
   virtualization logic; the new backend only needs to supply the param page and
   force the guest to use our vDSO. Without this, `--strict`/determinism is a lie
   for any time-reading program.

4. **Steal the shared-memory context queue + dual-spinning design** for any
   in-process backend. It is what makes systrap beat ptrace: the hot path avoids
   context switches by spinning on shared memory on *both* sides, with a
   load-aware controller and a futex deep-sleep fallback. `context_queue.go` +
   `sysmsg_lib.c` are a directly portable blueprint (Rust `AtomicU32` ring +
   cache-line padding). This maps onto Detcore's scheduler as the transport
   between the deterministic scheduler and guest threads.

5. **Keep a ptrace slow-path even in a "ptrace-free" backend.** systrap still
   uses `PTRACE_SYSEMU` for syscalls that can't be trusted through
   readable/writable shared stub memory (`syscall_thread.go:44-50`) and for full
   register/FP transfer. A pragmatic hermit backend can do the same: SUD/patch
   for the 99% fast path, a ptrace (or `seccomp_unotify`) executor for the awkward
   1%. Note systrap *also* offers a `seccomp_unotify`
   (`SECCOMP_FILTER_FLAG_NEW_LISTENER`) transport for the syscall thread
   (`syscall_thread.go:149`) as a ptrace-free alternative for the slow path —
   worth evaluating for a zero-CAP_SYS_PTRACE variant.

6. **Fail-closed patch protocol is reusable.** The 3-step non-atomic patch +
   `0x06`-guard + `HandleFault` restart (`usertrap_amd64.go`) is a
   battle-tested recipe for live-patching `mov;syscall` under concurrency without
   stop-the-world. If we do any instruction patching (SaBRe, e9patch, or
   SUD-fast-path), copy this protocol rather than inventing one.

7. **Determinism caveat to flag now:** systrap is built for *isolation +
   throughput*, not *deterministic scheduling*. Its dual-spinning, M:N
   worker/context multiplexing, and load-adaptive fast path are sources of
   nondeterministic interleaving. For hermit we would keep the **transport**
   (SUD trap + shared-mem handshake) but replace systrap's opportunistic
   scheduler with Detcore's deterministic scheduler — i.e. contexts become
   runnable to Detcore, not to a spinning worker pool. This is the same seam as
   the existing KVM `run_with_tool` Guest/Tool adapter (MEMORY
   `kvm-guest-tool-interface-works-e2e`).

---

## 8. Key source map (for follow-up)

- `systrap.go` — Platform/Context, `Switch`, `New`, `Requirements` (CAP_SYS_PTRACE).
- `subprocess.go` — `switchToApp` (hot path), `waitOnState` (sentry spin/futex),
  `handlePtraceSyscallRequest` (slow path), SUD enable (`:288`).
- `subprocess_linux.go` — stub fork, seccomp allow-list, SUD in seccomp rules.
- `filters.go` / `subprocess_amd64.go` — seccomp trap filters, vsyscall rules.
- `sysmsg/sighandler_amd64.c` — the SIGSYS/fault signal handler + patch detection.
- `sysmsg/syshandler_amd64.S` — patched-syscall fast-path trampoline (`%gs`-based).
- `sysmsg/sysmsg_lib.c` — context queue, spinning_queue, futex fallback.
- `usertrap/usertrap_amd64.go` — 3-step binary patch + `HandleFault`.
- `vdso/vdso_time.cc` + `pkg/sentry/loader/vdso.go` + `kernel/vdso.go` — vDSO + param page.
- Repo backend docs: `ai_docs/architecture-overview.md`,
  `ai_docs/sabre-determinism-analysis.md`, `ai_docs/hermit-v2-roadmap.md`,
  `ai_docs/e9patch-reverie-backend-vision.md`, `PROJECT_VISION.md`.
</content>
</invoke>

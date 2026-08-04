# Do patching backends need ptrace at all? — complete per-backend enumeration

**Task:** `do-patching-backends-need-ptrace-at-all` (P1, owner question).
**Author:** hermit-e9patch. **Date:** 2026-08-04.
**Grounding SHAs:** Hermit main `525627be` (HEAD moved to `8f656b4d` during the
session; `e9patch.rs` verified byte-identical); Reverie main
`79517704b0d19eeb3c4c234d0bfbe9f0a17c1199`; SaBRe
`41113f849f8799932ed8c7883f5a4de616b9e9fa`.

## Owner question and the definitional change it carries

> "Do we need ptrace AT ALL for patching backends — or can SIGSYS cover the whole
> surface? And ratcheting now includes REDUCING ptrace fallbacks, not just adding
> compat."

Two things, the second definitional:

1. **A compat regression is acceptable during architecture convergence.** The
   shared correct design — not the parity number — is the ratchet in this phase.
2. **Ratcheting gains a second axis:** reducing the number of cases that fall
   back to **ptrace (very-slow)** instead of being handled by **SIGSYS
   (semi-slow)**. A backend at the same compat % with fewer ptrace fallbacks
   **has ratcheted**. This was not previously measured at all.

## Answer (owner part c)

**NO fundamental ptrace case exists for the SYSCALL-INTERCEPTION surface in any of
the three patching backends (SaBRe, e9patch, LiteInst).** ptrace-for-interception
is a **work item, not an architecture requirement.**

The genuine ptrace-only residues are **not** syscall interception, in all three:

- **Lifecycle** (fork/exec/exit/wait reaping) → replaceable by pidfd / subreaper / wait.
- **RCB preemption timer + RCB clock** → exactly what the in-guest RCB accounting
  spec / bracketing-dance ([`ai_docs/in-guest-rcb-accounting-spec_20260804.md`](in-guest-rcb-accounting-spec_20260804.md))
  is designed to move in-guest via `rdpmc`.
- **CPUID / RDTSC instruction interception** (LiteInst Mode B, B6) → a separate
  *instruction*-interception surface, not syscalls.
- **Arbitrary signal delivery** → needs an in-guest signal shim.

## Reverie core-abstraction impact — CHANGE vs EXTEND (read before landing any follow-on)

**This enumeration is descriptive: it changes nothing.** But it must not hide the
distinction the owner cares about. The *design direction it endorses* splits into
two categories under the Reverie API Policy, and they cut in opposite directions:

**CHANGES a Reverie core abstraction — requires owner discussion BEFORE it lands
(not additive):**

1. **Syscall-interception model.** Replacing ptrace syscall-discovery with a
   SIGSYS→post-`sigreturn` in-guest handler plus a trusted syscall gate changes the
   *syscall interception/injection semantics* — a named core abstraction.
2. **Lifecycle ownership / container responsibilities.** Replacing ptrace
   fork/exec/exit/wait observation with pidfd/subreaper/wait + guest RPC changes
   *lifecycle ownership* — a named core abstraction.
3. **Tool hosting location + guest register/memory contracts (LiteInst
   Mode-B→Mode-A).** Moving the Tool in-guest and reading RCB via in-guest `rdpmc`
   bracketing changes where the Tool runs and the guest register contract. (The
   `rdpmc` primitive itself was additive, PR #363; *relying on it for scheduling*
   is the core change.)

**EXTENDS with new event types / activates dormant in-tree code — additive, report
loudly, no core-abstraction change:**

4. **Reverie ptrace-stats event types** (`PtraceSyscallEntry/Exit`,
   `PtraceRawSyscallRedirect`, `PtraceInstalledSigillDispatch/Marker`) — pure
   additive measurement; wiring Hermit to increment them is additive.
5. **Wiring the existing in-tree e9patch in-guest AOT fast path** insofar as it
   activates dormant code against existing abstractions — additive; **except** the
   `HybridPtrace` *lifecycle-owner* portion, which lands in category (2) above.

**Bottom line for the owner:** the analysis proposes NO change today, but the path
to "no ptrace needed" it points at **does change the syscall-interception model and
lifecycle ownership (categories 1–2) and the Tool/Guest execution + register
contracts (category 3)** — all requiring owner discussion before implementation.
It is not a pure additive-event-type extension. (The `api-extension` framing does
not apply cleanly; the load-bearing follow-ons are core-abstraction changes.)

## Two accounting boundaries (kept honest in both directions)

These come from the SaBRe half and are applied to all three backends so the
numbers do not inflate either way:

- **Do NOT count SIGILL/UD marker dispatch as a ptrace fallback.** It already runs
  in-guest.
- **Do NOT count trusted shared-object native execution as parity.** It is a
  native-execution hole the shared trusted-syscall-gate/SIGSYS design must close,
  not successful ptrace coverage.

---

## SaBRe (authoritative table, re-verified at 525627be)

Complete ptrace call surface: `hermit-cli/src/sabre_ptrace.rs`.

| case | current path | fundamental vs incidental | evidence |
| --- | --- | --- | --- |
| every kernel syscall, incl. already-rewritten/tool/runtime syscalls | VERY-SLOW ptrace entry+exit: supervisor resumes every tracee with `PTRACE_SYSCALL` | **INCIDENTAL tax.** Mode exists to discover residual raw sites; a seccomp `RET_TRAP`/SIGSYS filter plus a trusted syscall gate can select only misses. | `sabre_ptrace.rs:149-166,351-406,427-429` |
| residual raw syscall instruction at an UNTRUSTED mapping after coordinator readiness (decoder/coverage miss, post-load/JIT mapping) | ptrace entry reads RIP-2/mapping, writes `0f ff`, sets `orig_rax=-1`; ptrace exit restores syscall number/RIP so the new marker replays through guest SIGILL | **INCIDENTAL.** SIGSYS already supplies syscall/register/ucontext state; it can redirect after `sigreturn` to the shared in-guest handler. Existing SaBRe SIGILL proves the in-guest runtime can reconstruct the frame and call `runtime_syscall_router`. | `sabre_ptrace.rs:360-403,443-452,569-597`; `third-party/sabre/loader/loader.c:126-152` |
| insufficient bytes / unsafe neighbors for a five-byte jump | NOT ptrace: SaBRe decoder installs a UD marker and guest SIGILL dispatches it | **Already SEMI-SLOW in guest.** Patch geometry does not require ptrace. | `third-party/sabre/arch/x86_64/rewriter.c:129-177,253-275`; `loader/loader.c:126-152` |
| clone/fork/vfork child discovery and exec cache reset | ptrace lifecycle events | **INCIDENTAL, not syscall fallback.** In-guest SaBRe already owns clone/fork/vfork/clone3 injection and lazily registers children; guest RPC + pidfd/subreaper/wait can replace discovery. | `sabre_ptrace.rs:337-347,410-424`; `experimental/reverie-sabre/src/callbacks.rs:594-793` |
| final physical exit and signal death | ptrace wait status releases Detcore's physical-exit barrier | **Lifecycle, not interception.** SIGSYS cannot report an uncatchable death, but ptrace is not fundamental: launcher/coordinator can use wait/pidfd/subreaper. | `sabre_ptrace.rs:180-248`; `detcore/src/scheduler.rs:440-443,1570-1597,2256-2267` |
| arbitrary signal delivery / fatal diagnostics | ptrace signal stop reads regs/siginfo/maps then forwards the same signal | **Not a syscall fallback.** A full signal-event backend needs an in-guest signal shim; uncatchable-exit observation still uses wait/pidfd. Current behavior is diagnostic/forwarding only. | `sabre_ptrace.rs:253-325` |
| launch stop / ASLR handoff | `PTRACE_TRACEME`, detach+SIGSTOP, supervisor attach | **INCIDENTAL launcher coordination.** `personality(ADDR_NO_RANDOMIZE)` is already done in `pre_exec`. | `sabre_ptrace.rs:600-653` |

**Correctness gap (honest exclusion):** host fallback treats every shared-object
mapping plus SaBRe/plugin/bracket mappings as trusted and will NOT patch a
remaining raw syscall there, to avoid recursive tool RPC
(`sabre_ptrace.rs:525-537`). SaBRe initially scans only the main binary and the
known libc/librt/libpthread/libresolv set
(`third-party/sabre/loader/ld_sc_handler.c:38-41`; `loader/rewriter.c:830-870`),
and Hermit separately detours libc `getrandom` because that raw site is
deliberately missed (`detcore-sabre/src/lib.rs:244-275`). This is a
native-execution hole, not successful ptrace coverage.

**Measurement gap:** Reverie defines `PtraceSyscallEntry`, `PtraceSyscallExit`,
`PtraceRawSyscallRedirect`, `PtraceInstalledSigillDispatch`,
`PtraceInstalledMarker` (`experimental/reverie-sabre-stats/src/lib.rs:37-82`) plus
a host increment API (`:264-286`), but Hermit main's `sabre_ptrace.rs` never
maps/increments them; Hermit reports only distinct `patched_sites`
(`hermit-cli/src/lib.rs:1078-1082`). The new ratchet axis is named but not
actually measured on main.

---

## e9patch (verified myself)

**Mirror of SaBRe: ships ENTIRELY ptrace-hosted for interception today**, but the
in-guest fast path exists in-tree and is merely unwired.

| case | current path | fundamental vs incidental | evidence |
| --- | --- | --- | --- |
| every kernel syscall interception (steady state) | VERY-SLOW ptrace: `Backend::run` unconditionally runs the ptrace `tracer.wait()` event loop; the "injected-trap" tactic is still `int3`→ptrace stop (`eprintln` always prints `controller=ptrace`) | **INCIDENTAL.** In-guest AOT fast path exists in-tree but is unwired: `install_hybrid_runtime` returns `Unsupported` because the `HybridPtrace` lifecycle owner is a skeleton ("e9patch in-guest fast path not yet active and ptrace performs all event handling"). | `reverie-e9patch/src/backend.rs:988-1006,585-593`; `runtime.rs:246-262` |
| zero-site / unpatched raw syscall | ptrace executes the original syscall | **INCIDENTAL.** Same missing in-guest lifecycle owner; the in-guest AOT dispatcher (`reverie_e9patch_dispatch_aot`→`dispatch_direct`, `rt_sigreturn`→native tail) already handles patched sites in guest. | `backend.rs:604-608`; `aot.rs:171-196` |
| residual in-guest syscall dispatch | SEMI-SLOW SIGSYS, already handled in guest | **NOT ptrace** — already the semi-slow in-guest path. | `dispatch.rs:44-48,354-367` |
| insufficient bytes for patch (B0/SIGILL reservation) | REFUSED by policy (`--tactic-B0=false`) | **Policy choice, not a ptrace requirement.** | `hermit-cli/src/e9patch.rs:323-329`; `rewrite.rs:281-287` |
| generic-Tool signal-context hosting (A8 alloc/lock/block), fork/exec (B2), timer (B3), `rt_sigreturn` (C1), signal-state (C2) | ptrace host | **INCIDENTAL for interception.** These are LIFECYCLE / PREEMPTION / SIGNAL concerns of the in-guest host, NOT syscall interception. Blocked by the missing `HybridPtrace` lifecycle owner + generic-Tool hosting. | `runtime.rs:246-262` (in-guest host skeleton) |

**Verdict:** NO fundamental interception case. Ptrace-for-interception is blocked
by the missing `HybridPtrace` lifecycle owner + generic-Tool hosting, not by any
structural impossibility. The "injected-trap" NAME misleads — it is still ptrace
(`controller=ptrace`), not in-guest.

---

## LiteInst (verified myself — corrects a subagent gap)

**Two modes.** Mode A is pure in-guest with ZERO ptrace; Mode B is ptrace-hosted.
**Hermit drives Mode B, not A** — the trap that a `.run_host` grep misses.

- **Mode A (pure in-guest, zero ptrace):** `Backend::run`→`run_with_preload`→
  `launch`, no `Tracer` (`backend.rs:480-489,353`). Intercepts everything
  in-guest: seccomp→SIGSYS discovery (A1) then in-guest trampoline
  `tool_trampoline`/`process_syscall` (`runtime.rs:1554-1600`). In-guest
  "fallbacks" FAIL CLOSED to `errno`, never escalating to ptrace
  (`runtime.rs:1691-1700`, `EOPNOTSUPP`). Ships via `reverie-examples`
  (`run_with_output_and_preload_data`).
- **Mode B (ptrace host — what Hermit actually calls):**
  `hermit-cli/src/lib.rs:1534` and `:1643` call
  `LiteinstBackend::run_host_with_preload::<Detcore>` (turbofish `::` — a
  `.run_host` grep MISSES it). The ptrace host owns the sole Tool/GlobalTool
  (`backend.rs:199-237`); every hooked syscall round-trips to the supervisor
  (seccomp-stop discovery B1 `task.rs:3722-3819`; `int3` `HOST_SYSCALL_MARKER`
  steady state B2 `runtime.rs:1536-1552` + `task.rs:2010-2088`). Triggered by
  `REVERIE_LITEINST_HOST_RUNTIME=1` (`backend.rs:468`, `runtime.rs:376`).

**Why Mode B:** RCB preemption timeslices (test literally named
`liteinst_host_backend_preserves_ptrace_rcb_timeslices`, `lib.rs:1972`) + CPUID/
RDTSC (B6) — **NOT interception.** Patch-install-via-ptrace
(`reverie_liteinst_install_site_for_ptrace`, `runtime.rs:1154-1237`, Quiescent
publication) is **not fundamental:** in-guest Concurrent publication of
cross-cache-line straddlers works with calibrated `STRADDLER_STALENESS_TICKS`
(`straddler.rs:74-91`); quiescence is a no-calibration robustness convenience.

**Verdict:** NO fundamental interception case; Hermit's ptrace dependence is for
RCB scheduling + CPUID/RDTSC, both non-interception, and RCB has the designed
in-guest replacement (bracketing-dance spec).

---

## Second ratchet axis (reducing ptrace fallbacks)

Independent of compat %:

- **SaBRe** already runs the Tool in-guest; ptrace only discovers/patches residual
  raw sites.
- **e9patch** measures nothing in-guest today — 100% ptrace. Wiring the in-guest
  `HybridPtrace` lifecycle owner is the ratchet.
- **LiteInst under Hermit** is 100% Mode-B ptrace interception today. Moving Hermit
  to Mode A (needs the in-guest RCB timer from the bracketing-dance spec + in-guest
  CPUID/RDTSC + lifecycle) is the ratchet.

## Traps paid (record for the next agent)

1. **turbofish `::` vs `.` in grep** hid Hermit's Mode-B call — grep
   `Backend::run_host`, not `.run_host`.
2. **e9patch "injected-trap" NAME misleads** — it is still ptrace
   (`controller=ptrace`), not in-guest.
3. **Hermit HEAD moved mid-session** (`525627be`→`8f656b4d`); `e9patch.rs`
   identical, other files may drift — re-verify line numbers against current HEAD.

## Concrete follow-on work item

Moving Hermit's LiteInst Mode-B→Mode-A requires the in-guest RCB timer the
[bracketing-dance spec](in-guest-rcb-accounting-spec_20260804.md) designs — that
is the concrete work item that makes "no ptrace needed" *real* for LiteInst. The
e9patch analogue is wiring the `HybridPtrace` lifecycle owner.

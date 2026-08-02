# LiteInst native (off-ptrace-host) path: architecture reality + re-scoped plan

- Date: 2026-08-01
- Agent: hermit-243 (native-path impl lane under `liteinst-flagship-acceleration`)
- Coordinating with: hermit-liteinst (blocker diagnosis, owns `worktrees/liteinst`),
  hermit-sabre (ptrace machinery / DRY), hermit-242 (liteinst optimization research)
- Grounding SHAs: Hermit slot base `origin/main`; Reverie pinned rev
  `aa6f1283aeee3efd174c57f6dd8198310bd307e1` (rrnewton/reverie) — the source the
  hermit build compiles (`detcore/Cargo.toml`, `hermit-cli/Cargo.toml`;
  `reverie-liteinst` `default-features = false`).
- Evidence base: full architecture map by code-search agent over the pinned
  reverie checkout + `liteinst2/` + SaBRe at `experimental/reverie-sabre`.

## SECOND CORRECTION (2026-08-01, authoritative) — read this first

**The "Architecture reality" section below is WRONG about what `hermit` runs.**
It says `hermit run --backend liteinst` is "already standalone off ptrace" via
`LiteinstBackend::run` → `run_with_preload`. **hermit-cli never calls that trait
method.** At the corpus SHA `464cbd9f`, `hermit-cli/src/lib.rs:1523` and `:1632`
dispatch LiteInst directly to `run_host_with_preload::<Detcore>` /
`run_host_with_output_and_preload::<Detcore>` — the **ptrace host** (TracerBuilder
tracer owns Tool/GlobalTool from exec; preload only injects hot-site traps,
backend.rs:196-205). The standalone `run_with_preload` path is exercised only by
`reverie-liteinst/tests/hybrid.rs:907` with a `PassthroughGetpid` tool — **never
Detcore, never via hermit.**

Consequence: the 117/200 corpus was measured on the **ptrace-host hybrid**, which
per its own contract is **single-process, single-thread; fork/vfork/clone fail
closed**. The flagship "native standalone off-ptrace" claim is therefore
**unmeasured with Detcore** today. The gap list below is still useful for the
standalone path, but the "already the shipped default / not blocked" framing is
false — the ptrace host is what ships. Full corrected answers:
`ai_docs/liteinst-flagship-verification-answers_20260801.md`.

## CORRECTION vs. an earlier draft of this doc (now itself superseded — see above)

An earlier draft asserted "today LiteInst is ptrace-hosted; flagship = get off
ptrace." That draft's *premise* was closer to hermit's real dispatch than the
"Architecture reality" rewrite below; the rewrite over-corrected by reading the
reverie trait method instead of hermit's actual call site. The `TracerBuilder`/
`RpcServer` code is **not** merely an opt-in diagnostic for hermit — it is the
path hermit uses.

## Architecture reality (grounded)

The default `hermit run --backend liteinst` path is **already standalone off
ptrace**:

- `LiteinstBackend::run` (`reverie-liteinst/src/backend.rs:389`) → `run_with_preload`
  → `launch<T>` (backend.rs:466): in-process `GlobalState`
  (`init_global_state`, backend.rs:493) served over a Unix-domain socket
  (`RpcServer::bind_with_connection_readiness`, backend.rs:495); child launched
  with `LD_PRELOAD` set and a **plain `child_command.spawn()`** (backend.rs:537)
  — **no `PTRACE_TRACEME`, no tracer**.
- In-guest interception is two-tier, both in-guest:
  1. **First touch (slow path)** = seccomp `SECCOMP_RET_TRAP` + SIGSYS
     (`reverie-preload/src/trap.rs`); `LiteinstDispatcher` (runtime.rs:1537)
     claims the site, installs a **liteinst2 trampoline**, and
     `event.defer_to(trampoline.address())` (runtime.rs:1593).
  2. **Steady state (fast path)** = the patched site jumps to the liteinst2
     trampoline whose callback `installed_syscall_hook` (runtime.rs:1469) builds
     a `SyscallEvent` directly from the liteinst2 `HookContext` (no signal) and
     routes to the Detcore Tool via `tool_host::dispatch` (runtime.rs:1667).
- The Detcore **Tool runs in-guest**; only `GlobalTool` calls cross the UDS RPC.
  Register bridge = `LiteinstGuest::regs()/set_regs()` reading/writing the
  liteinst2 `HookContext` (`tool_host.rs:472-532`).

So "native standalone off the ptrace host" is **the shipped default**, working
today for single-process, dynamically-linked, non-`exec` guests (~117/200 L2 at
the pin). It is **not** blocked and **not** greenfield.

## The real delta to a *fully* standalone backend (the flagship gaps)

Everything downstream (liteinst2 punning patcher, trampolines, register bridge,
in-process coordinator, dispatcher seam) is built and working. The remaining gaps
(from the map, Q5) are:

1. **Loader/startup pre-constructor gap** — the in-process seccomp filter cannot
   cover the ~40 loader syscalls before the constructor runs, `AT_SECURE`, static
   binaries, or vDSO fast paths (by design; `lib.rs:29-35`, `lifecycle.rs:17-21`).
2. **`HybridPtrace` is a stub** — `reverie-preload/src/lifecycle.rs:95-109`
   returns `Unsupported`. It is the intended **thin pre-exec ptrace owner** that
   would close gaps 1/3/4/5.
3. **`execve`/`execveat` fail closed** — `ENOTSUP` (runtime.rs:1651;
   dispatch.rs:215): the runtime cannot re-establish across `exec`.
4. **Process creation fails closed** — `clone3`/`vfork`/`clone`-with-stack →
   `ENOTSUP`/`EPERM` (runtime.rs:1628/1691; tool_host.rs:416). Plain `fork`
   already works (atomic filter inheritance + `fork_hook`). This is *the*
   single-process limiter.
5. **vDSO not intercepted on the LiteInst path** — no `handle_vdso` equivalent;
   clock/gettimeofday via vDSO bypass the trap → time determinism hole.
6. **No RCB/PMU timer delivery** — `set_timer/read_clock` return `Unsupported`
   (tool_host.rs:616-638) → no preemption-based scheduling/chaos under LiteInst.
7. **Per-site SIGSYS bootstrap** — the fast path is reached only after one SIGSYS
   per site; a "pure fpatchable" pre-patch model would need AOT/startup site
   discovery (P3 bonus, not the enabler).

Gaps 3/4 (exec + clone) are what force single-process; gap 6 is what blocks
preemption-based L2 scheduling parity. Both 1/3/4/5 are exactly what a thin
`HybridPtrace` pre-exec/exec/clone/vDSO owner is designed to close.

## DRY with SaBRe — narrowed

The earlier "share the whole ptrace-hybrid backend with SaBRe" framing is too
broad. Grounded facts:

- LiteInst's flagship path uses **no ptrace** — it is seccomp+SIGSYS+liteinst2
  trampoline. The shared abstraction it already sits on is
  `reverie-preload::{dispatch, lifecycle, trap}` (also consumed by the AOT/e9patch
  `dispatch_direct` seam and the standalone cdylib built-ins).
- SaBRe is architecturally **opposite** on the decisive axis:
  `SabreGuest::set_regs` **rejects** RIP/RSP/flags changes
  (`reverie_adapter.rs:1171`, `EOPNOTSUPP`), because SaBRe resumes through an
  internal scratch trampoline; liteinst2's `HookContext` is **designed** to allow
  RIP/RSP/RFLAGS edits. So the *instrumentation + guest-register* layers cannot be
  shared.
- Where DRY **does** legitimately apply: the **thin-ptrace lifecycle mechanism**
  for gaps 1/3/4/5. SaBRe already has a ptrace safety-net (`hermit-cli/src/
  sabre_ptrace.rs`: syscall-stop trapping, marker rewrite, orig_rax gating) and a
  loader. If `HybridPtrace` is implemented as a thin pre-exec/exec/clone/vDSO
  ptrace owner, that mechanism (spawn+TRACEME, wait-exec-stop, install seccomp
  pre-main, PTRACE_EVENT_{EXEC,CLONE,VFORK} handling, vDSO discovery/patch) is the
  reusable core — factor it once in `reverie-preload::lifecycle` and let both the
  LiteInst `HybridPtrace` controller and SaBRe consume it. The instrumentation and
  set-RIP semantics stay backend-specific.

## GOVERNANCE — why this is a checkpoint, not an immediate implementation

Implementing `HybridPtrace` is a **Reverie core-abstraction / syscall-interception
+ lifecycle-ownership change**. That is:
- post-facto-human-review **trigger #2** (Reverie API/core-abstraction: the
  interception/lifecycle model), and
- explicitly gated by the Reverie API Policy: *"Discuss the design with the user
  before implementation when a proposal changes ... syscall interception/injection
  semantics ... lifecycle ownership."*

Additionally, the owner's SaBRe-hybrid **fallback was conditional** ("*if* the
LD_PRELOAD trampoline→callback path is *blocked*"). The map shows that path is the
working default — the condition is **not met** — so charging into a SaBRe-shared
ptrace-hybrid under that conditional authorization would be building on a
falsified premise.

Therefore this doc ends at a **design checkpoint**: the accurate picture + a
re-scoped recommendation, surfaced to the owner before writing the core change.

## Re-scoped recommendation (for owner confirmation)

Fastest path to *more* flagship coverage on the already-native backend, in order:

- **A. Multiproc via `HybridPtrace` thin ptrace owner** (gaps 1/3/4/5): closes
  exec + clone/vfork + pre-main + vDSO in one additive lifecycle controller;
  factor the thin-ptrace mechanism to share with SaBRe (DRY as narrowed above).
  Biggest coverage unlock; largest/most-gated change (Reverie core → trigger #2,
  pre-impl discussion required).
- **B. vDSO interception on the LiteInst path** (gap 5): additive dispatcher-level
  determinism fix; smaller, still interception-semantics (discuss).
- **C. RCB/PMU timer delivery** (gap 6): unlocks preemption/L2 scheduling parity;
  scheduling-adjacent (trigger #4-adjacent).
- **P3 bonus** (gap 7 / `impl-liteinst2-fpatchable`): AOT pre-patch site
  discovery to drop the per-site SIGSYS bootstrap — an optimization, not the
  enabler; owner already de-scoped it.

Align the precise first target with **hermit-liteinst's diagnosis** (task
`liteinst-flagship-blockers-diagnosis`), which is enumerating which of the ~82
non-passing corpus cells fail for which of these reasons — that tells us whether
multiproc (A) or vDSO/time (B) or preemption (C) unlocks the most cells first.

## Preserve-not-delete inventory (owner directive)

Branch scan: **no** pre-existing native-LD_PRELOAD/trampoline WIP branch for
LiteInst — nothing to rescue; the native path is the live default, not parked WIP.
Existing seams to keep (all WIRED-IN or explicitly parked, none deletable):
native trampoline dispatch (runtime.rs:1469/1537/1612/1648/1667), register bridge
(tool_host.rs:472-532), liteinst2 trampoline+patcher core, the
`SyscallEventSource::DirectInstrumentation`/`defer_to` dispatch contract
(dispatch.rs), `dispatch_direct` AOT seam (trap.rs:154, dormant), the opt-in
ptrace-host hybrid (backend.rs:207, dormant), and the `HybridPtrace` stub
(lifecycle.rs:95, the parked seam to extend). Existing SaBRe native WIP to
coordinate with, not duplicate: rrnewton/reverie `codex/sabre-native-syscall-clobbers`,
`codex/sabre-vfork-native-gate-r4`.

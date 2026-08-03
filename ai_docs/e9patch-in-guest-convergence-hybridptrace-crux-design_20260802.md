# e9patch in-guest convergence: the HybridPtrace crux — grounded design

Date: 2026-08-02
Author: impl agent, opus-4.8 (e9patch lane)
Status: **design for discussion** — NOT implemented. The change this doc
describes is a core Reverie *lifecycle-ownership* change, which the workspace
Reverie API Policy requires be discussed with the owner before implementation,
and which is `post-facto-human-review` trigger #2. This artifact is the
design-discussion input, not a green light to code.

## TL;DR

The e9patch "in-guest convergence" the directive asks to *continue* is, on the
architecture, **already merged** on main: e9patch reuses the shared
`reverie-preload` in-guest runtime (LD_PRELOAD + seccomp/SIGSYS trap + dispatch)
and already hosts Noop/Strace/Counter1 tools in-guest on its crate-internal
direct path. What is *not* done — the single blocker gating the whole thing from
the hermit CLI — is one function: `reverie_preload::lifecycle::HybridPtrace::
install`, which today returns `io::ErrorKind::Unsupported`. Everything downstream
of it (e9patch `install_hybrid_runtime`, `RuntimeMode::HybridPtrace`, the
ToolHost, the fork-reset dispatcher, the RPC transport, the sealed-memfd
bootstrap) is implemented and merely gated behind that one `Err`.

Implementing it is not a mechanical unblock: HybridPtrace splits ownership across
a **guest-side** installer (runs in the guest, sets up the in-process SIGSYS hot
path) and a **supervisor-side** ptrace owner (installs a pre-`exec` seccomp
filter, closing the loader/startup gap and handling `exec`/`clone`/`vfork` +
vDSO). That supervisor seam is the same work deferred as "SaBRe real-backend
work," and it is precisely a lifecycle-ownership change. Hence: design first.

## Where the code actually is (grounding)

All paths relative to the `reverie`/`hermit` submodules at current main.

### The crux

- `reverie-preload/src/lifecycle.rs`
  - `trait LifecycleController { fn name(); unsafe fn install(&self, cfg) }`
  - `struct InProcessSeccomp` — **working** `install`: `trap::install_handler`
    then `SeccompFilter::for_trusted_gate(trap::trusted_gate())` +
    `filter.install()`. This is the live in-guest path.
  - `struct HybridPtrace` — `install` returns
    `Err(io::Error::new(ErrorKind::Unsupported, "hybrid-ptrace lifecycle
    controller is not yet implemented; use InProcessSeccomp or the SaBRe real
    backend"))`. The docstring (lines ~82–93) specifies the real requirements
    (below). A unit test asserts `hybrid_is_not_yet_installable`.

### Downstream, already implemented and gated behind the crux

- `reverie-e9patch/src/runtime.rs`
  - `enum RuntimeMode { InProcessFallback, HybridPtrace }`
  - `install_runtime()` — works, drives `InProcessSeccomp`.
  - `install_hybrid_runtime()` — returns the `Unsupported` stub (delegates to the
    crux).
  - `install_with_controller()` — already calls
    `reverie_preload::install(Box::new(E9patchDispatcher::with_fork_reset()),
    controller, &config)` (carries two `AUTONOMOUS-BOT-IMPLEMENTED` tags).
  - `initialize_from_environment()` — precedence TOOL_ENV → RUNTIME_ENV → inert;
    `HybridPtrace` arm present.
- `reverie-e9patch/src/tool_host.rs` — `ToolHost<T>` with
  `tool: SpinMutex<Option<T>>`; `E9patchGuest impl Guest`;
  `injected_syscall_guard`.
- `reverie-e9patch/src/backend.rs` — the **live default** `impl Backend::run<T>`
  is spawn + `tracer.wait()` = **pure ptrace**. The in-guest
  `run_direct*`/`launch_direct<T>` paths exist but are off the default path
  (exercised only by the crate's own tests).

### The CLI collapse to pure ptrace

- `hermit/hermit-cli/src/bin/hermit/run.rs`
  - `runtime_backend()` maps `Backend::E9patch -> Backend::Ptrace` (a test
    asserts this). So the hermit CLI e9patch path is AOT rewrite (e9tool) +
    **ptrace** runtime; the in-guest fast path is unreachable from the CLI today.
  - `prepare_e9patch_program()` prints ":: Backend: e9patch preprocessing +
    ptrace runtime" — accurate.

### The proven in-guest reference: LiteInst

LiteInst already ships **two** in-guest runtimes and is the concrete template:

- `reverie-liteinst/src/backend.rs`
  - `run_host_with_preload<T>` (live hermit CLI path): a **ptrace-host hybrid**
    via `TracerBuilder::<T>::new(command).liteinst_runtime(preload,
    HOST_BEGIN_MARKER, HOST_READY_MARKER, HOST_HELPER_RETURN_MARKER,
    HOST_SYSCALL_MARKER)`. This is the closest existing analogue to what
    HybridPtrace must build for e9patch.
  - `run_with_preload<T>` / `launch::<T>` (pure in-guest coordinator path):
    tempdir UDS `coordinator.sock`, `reverie_rpc_transport::RpcServer`,
    sealed-memfd bootstrap (magic `REVERIE-LI-V1`), LD_PRELOAD child. This is the
    template for e9patch's out-of-process GlobalState wiring.

## What HybridPtrace must actually do (from the docstring + architecture)

1. Launch the guest under a **thin ptrace controller** that installs a
   **pre-`exec` seccomp filter**, closing the loader/startup gap so static
   binaries and `exec`'d children are covered from instruction zero (the
   in-process SIGSYS handler cannot self-install before its own code runs).
2. Keep the **in-process SIGSYS trap on the hot path** for the common case
   (this is the whole point — avoid a ptrace round-trip per syscall).
3. Let the controller handle the events the in-process handler cannot:
   `exec`/`clone`/`vfork` stops (re-arming the guest-side runtime in the new
   image/address space) and **vDSO patching**.

The ownership split is the crux's difficulty: `LifecycleController::install`
runs **in the guest**, but the ptrace owner must be the **supervisor**. So
HybridPtrace is not one function — it is a coordinated guest-side + supervisor-
side (reverie-ptrace) change. That supervisor seam == the deferred "SaBRe
real-backend" work, and it is a lifecycle-ownership change (policy-gated).

## Confirmed secondary blockers (must be sequenced, not skipped)

Even with HybridPtrace installed, these cap what the in-guest path can run and
must be surfaced honestly rather than silently:

1. **ToolHost spinlocks → single-thread only.** `SpinMutex<Option<T>>` /
   `SpinMutex<HashMap>` in `reverie-liteinst/src/tool_host.rs` and
   `reverie-e9patch/src/tool_host.rs`. Multi-threaded guests will deadlock/spin.
2. **Fail-closed multiprocessing.** `injected_syscall_guard` rejects
   `clone`/`fork`/`vfork`/`execve`/`execveat`
   (`reverie-e9patch/src/tool_host.rs`, `reverie-liteinst/src/tool_host.rs`,
   shared `PassthroughDispatcher::apply_guards` in
   `reverie-preload/src/dispatch.rs`). **Consequence:** multi-process tools
   (e.g. Counter2, which forks an exec tree) **cannot** be exercised in-guest
   yet. "Counter2 next" is ruled out until this is lifted.
3. **Synchronous future driving.** `Waker::noop()` in the ToolHost future driver
   — no real async wakeups; blocking tool futures cannot suspend/resume properly.

## Proposed staged rollout (for discussion — do not implement yet)

Stage the risk; each stage is independently reviewable and keeps the CLI default
(pure ptrace) untouched until proven.

- **Stage bootstrap-seam** — implement HybridPtrace as a *supervisor-owned*
  controller reusing the liteinst `TracerBuilder::liteinst_runtime` pattern:
  supervisor installs the pre-`exec` seccomp filter and owns
  `exec`/`clone`/`vfork` + vDSO; guest-side keeps `InProcessSeccomp`'s hot path.
  Gate behind an opt-in flag; CLI still defaults to ptrace. Target: single-thread
  single-process guests reach in-guest hot path with byte-identical DETLOG to
  ptrace (the corpus in PR #1507 and the 20 existing guests are the L2 oracle).
- **Stage single-proc-corpus-parity** — run the freestanding corpus through the
  HybridPtrace path and prove `mapped==candidate`, `b0==0`, L2, and DETLOG
  tail-match hold with the *in-guest* runtime, not just ptrace.
- **Stage MT** — replace ToolHost `SpinMutex` with a real lock + fix the
  `Waker::noop` future driving; unblock multi-threaded guests.
- **Stage multiproc** — lift `injected_syscall_guard`'s fail-closed rejection
  with correct fork/exec re-arming; only then is Counter2-class in-guest work
  possible.

Each stage that touches the Tool/Guest/Backend/lifecycle contract is
individually a `post-facto-human-review` trigger and a Reverie-API-Policy design
point.

## Determinism follow-up surfaced while corpus-building (2026-08-02)

Building relocation-stress guests for PR #1507 surfaced a benign-but-worth-noting
observation: a **looped single rewritten site** surfaces **once** in e9patch's
`--log=info` inbound-syscall stream, vs. N times under ptrace. Verified this is
log/interception *granularity*, not a determinism hole: a guest looping
`clock_gettime(CLOCK_MONOTONIC)` 4× is **byte-identical** golden-vs-e9patch and
passes L2 (virtual time is determinized on every iteration; no host-time leak).
The corpus driver's DETLOG tail-match heuristic assumes one inbound line per
guest syscall, so a looped-site guest trips it despite being L2-correct — hence
the loop guest was dropped from PR #1507. **Open question for the in-guest
convergence work:** confirm that under the *future HybridPtrace in-guest hot
path* (not today's ptrace runtime), a looped determinization-requiring syscall
(time/random) is still determinized on *every* iteration and never serviced from
a stale first-hit cache. This is a cheap, high-value L2 probe to add once the
in-guest path is CLI-reachable.

## Recommendation

1. **Do not blind-implement HybridPtrace.** It is lifecycle-ownership; per policy
   it needs an owner design discussion first. This doc is that input.
2. **Corpus growth is landing-bottlenecked, not authoring-bottlenecked.** ~28
   CLEAN `*-families` breadth PRs (rounds 29–56) sit unlanded atop 20 guests.
   Adding rounds grows the backlog and fixes no defect (compat is saturated at
   183/184, sole gap `rcx-canonicalization`, inherent). The coordinator decision
   is cascade-land-from-#1220 vs. halt-and-close, not "author round 57." PR
   #1507 is deliberately a small, orthogonal *engine-stress* addition off
   `origin/main`, not another stack entry.
3. If the owner approves HybridPtrace, start at **Stage bootstrap-seam**, reusing
   the liteinst `TracerBuilder::liteinst_runtime` template, gated behind an
   opt-in flag with the CLI default unchanged.

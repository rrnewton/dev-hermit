# Feasibility: one patching-backend constructor agnostic on patching METHOD

**Task:** `unified-patching-backend-constructor-feasibility`. **Author:**
hermit-e9patch (opus-4.8), 2026-08-03. **Evidence base:** the ground-truth audit
`experiments/backends_md_ground_truth_audit_20260803/README.md`, all re-verified
against reverie primary **HEAD `d2fb9a05`** with `file:line`.

## Verdict (one paragraph)

**Conditional YES for liteinst + e9patch, NO for an immediate flat three-way
merge, and sabre is a separate track.** A single in-guest constructor
parameterized by a patch-method strategy is feasible for liteinst and e9patch —
in fact it is *partly already built* — but only after one hard prerequisite is
met that unification itself does not deliver: a **working in-guest Detcore fast
path** (RCB clock/preemption + scheduler park + a real lifecycle owner). Until
that exists, "unify the in-guest constructor" unifies two immature/inert paths
while the one wired, tested Detcore path (liteinst host-side Mode B) stays on
ptrace. The owner's provisional grouping — *liteinst+sabre unify, e9patch
converges* — is **inverted by the code**: at the substrate level liteinst and
e9patch already share `reverie-preload`; **sabre is the outlier that shares
none of it.**

## The sharp test applied: WHERE the tool lives vs HOW it patches

| Backend | Wired/production tool location | In-guest substrate shared? | Patch method |
| --- | --- | --- | --- |
| **liteinst** | **host-side** (Mode B `run_host_with_preload::<Detcore>`, ptracer) — the only wired Detcore path | **YES** — `LiteinstDispatcher: SyscallDispatcher`, `CoordinatorRpc`, `LiteinstGuest<T>` on `reverie-preload` (Mode A) | runtime in-place patch (LiteInst2 replace-first) |
| **e9patch** | **host-side** (maps `E9patch→Ptrace`, `run.rs:1714`); in-guest ToolHost inert | **YES** — `E9patchDispatcher: SyscallDispatcher`, own `CoordinatorRpc`, `E9patchGuest<T>` on `reverie-preload` | AOT `e9tool` rewrite at prepare time |
| **sabre** | in-guest plugin; real Detcore via out-of-tree `libdetcore_sabre.so` | **NO** — touches `reverie-preload` not at all; own C loader, adapter, RPC, `SabreGuest`; adapter is first-poll-only | load-time 5-byte JMP + SIGILL fallback |

Reading the table with the sharp test ("differs in WHERE, not HOW ⇒ not a method
variant"): **liteinst and e9patch differ in HOW** (both plug the same
`SyscallDispatcher` seam over the same runtime; only the site-redirect mechanism
differs). **sabre differs in WHERE the machinery lives and HOW it is loaded** —
its adapter cannot even host an async tool — so it is a different architecture,
not a third method.

## Q1 — What is genuinely COMMON (verified)

1. **`reverie-preload` runtime is the backend-agnostic seam and already exists.**
   `reverie-preload/src/dispatch.rs` doc: *"Every ld-preload backend (e9patch,
   liteinst) and every hosted tool plugs in by implementing `SyscallDispatcher`.
   The runtime owns the seccomp/SIGSYS plumbing; the dispatcher owns the
   policy."* Implementors today: `LiteinstDispatcher` (`reverie-liteinst/
   src/runtime.rs:1622`), `E9patchDispatcher` (`reverie-e9patch/src/dispatch.rs:
   354`), plus preload built-ins `Passthrough`/`SpoofGetpid`. The runtime already
   models both entry styles: `SyscallEventSource::{SignalTrap,
   DirectInstrumentation}` (`dispatch.rs`) — i.e. the seam was designed for
   multiple patch methods.
2. **The in-guest host is a near-duplicate across liteinst and e9patch.**
   `LiteinstGuest<T>` (`reverie-liteinst/src/tool_host.rs:391`, file 732 LOC) and
   `E9patchGuest<T>` (`reverie-e9patch/src/tool_host.rs:505`, file 1218 LOC) each
   `impl Guest<T>` and each wrap `CoordinatorRpc<T::GlobalState>` over the same
   preload trap/dispatch. These are two copies of one design — the single
   strongest piece of evidence that one generic `PreloadToolHost<T>` is feasible.
3. **GlobalState is a host singleton reached by RPC — universal.** True for every
   in-guest backend (intrinsic to Detcore determinism: one logical CPU,
   deterministic thread order, RCB preemption). Not a differentiator.

## Q2 — What VARIES and expresses cleanly as a strategy trait

- **Patch method = "how a syscall site is redirected to the dispatcher."**
  liteinst: first hit via SIGSYS, then a runtime replace-first patch installs a
  trampoline (`runtime.rs:1622-1701`). e9patch: `e9tool` rewrites sites AOT so
  they call the dispatcher directly; residual sites fall to SIGSYS. Both funnel
  into the *same* `SyscallDispatcher`. This is expressible as a `PatchStrategy`
  trait — roughly `prepare(artifact)`, `install_site(addr)`,
  `residual_trap_policy()` — with `LiteinstDispatcher`+its patcher and
  `E9patchDispatcher`+e9tool as the two implementations. The event-source tag
  already in preload is exactly the runtime hook such a trait needs.

## Q3 — What RESISTS unification, and WHY (named, not glossed)

1. **No in-guest Detcore scheduler exists in ANY in-guest path — the load-bearing
   blocker.** `Guest::set_timer` / `set_timer_precise` / `read_clock` return
   `Unsupported` in BOTH hosts (`reverie-e9patch/.../tool_host.rs:632-648`;
   liteinst `tool_host.rs:616-638`). No RCB clock ⇒ no deterministic preemption
   ⇒ Detcore's scheduler cannot run in-guest. This is common to all three, not a
   method difference; unification cannot paper over it and does not build it.
2. **Lifecycle ownership is unimplemented.** `HybridPtrace` — the controller meant
   to let an in-guest fast path coexist with a ptrace lifecycle owner for
   clone/fork/vfork/exec/vDSO/pre-constructor — returns `Unsupported`
   (`reverie-preload/src/lifecycle.rs:97-105`, asserted by test `:125`). Every
   in-guest ToolHost fails closed on clone/fork/exec (e9patch
   `tool_host.rs:656-671`; liteinst `task.rs:3935-3978` for Mode B). Two
   interception mechanisms cooperating over one process tree is genuinely hard.
3. **sabre is a different substrate.** It uses `reverie-preload` nowhere; it owns
   a C loader ABI, thread registry, signal/SIGILL machinery, memory adapter and
   `SabreGuest`, and a **first-poll-only** adapter (`reverie_adapter.rs:161`
   polls each async handler once, dropping any `Pending`) that cannot run async
   Detcore at all. Adopting the shared constructor means replacing that adapter
   with the preload runtime — which BACKENDS.md itself says conflicts with
   SaBRe's loader, recursion/TLS routing, and SIGILL handling. That is a rewrite,
   not a parameterization.
4. **e9patch's production path is WHERE-different today.** As wired,
   `--backend e9patch` runs on ptrace (`run.rs:1714`; `lib.rs:975`), DETLOG
   host-side; its in-guest ToolHost has no in-tree caller, no clock, single-proc.
   e9patch must *become in-guest* before its AOT method can slot into a shared
   in-guest constructor. Convergence is a precondition, not a byproduct.

## Q4 — Risk to the flagship

liteinst **Mode B** (host-side ptracer) is the only wired, tested,
Detcore-carrying patching path, and it is **not** the in-guest constructor path
(Mode A / ToolHost). Therefore:

- Building the shared in-guest constructor does **not** touch Mode B — good, it
  will not destabilize the flagship; but it also does **not** advance it. It
  builds the in-guest future in parallel.
- **The bad trade to refuse explicitly:** rerouting liteinst's *production* path
  onto a new shared in-guest constructor to achieve "unification." That
  destabilizes the one working Detcore path for an in-guest fast path that cannot
  yet run the scheduler, with **no measured perf justification**. Perf leadership
  is a hypothesis, and the cost model forbids assuming it: the **(a)
  sequentialization** cost — park + RPC to the global scheduler singleton — is
  present in every backend and cannot be reduced by in-guest trapping; only **(b)
  the trap round-trip** can improve, and only for events serviceable without a
  scheduling decision — a fraction that is **currently unmeasured**. No
  reroute of Mode B until (i) an in-guest path runs full Detcore incl. scheduler
  and (ii) shows a measured (b) win with (a) held equal.

## Q5 — Sequencing

e9patch converges to in-guest **first**; unification does **not** carry the
convergence. Note the honest reframe: **a de-facto unification already exists
today — at the HOST (ptrace) layer**, where liteinst Mode B and e9patch both
funnel to the ptracer. The owner's desired unification is the *in-guest,
perf-leading* one, and it is gated on the blocker in Q3.1.

## Proposed milestone — `in-guest-patching-backend-unification` (staged, gated)

- **Stage 1 — prove ONE in-guest Detcore fast path (HARD GATE; this is the real
  blocker, not merging).** On liteinst Mode A (most mature; already has ToolHost
  + a runtime patcher): implement in-guest RCB clock/timer, scheduler park over
  RPC, a working `HybridPtrace` lifecycle owner (clone/fork/exec/vDSO/pre-exec),
  and multi-process support. Measure vs ptrace with (a)/(b) **separated**.
  *Exit:* full Detcore runs in-guest at L2 on a multi-proc corpus AND shows a
  measured (b) advantage. **If Stage 1 fails, the answer is NO** — keep ptrace as
  the shared substrate (the existing de-facto unification) and mark the in-guest
  ToolHosts experimental.
- **Stage 2 — extract the shared constructor.** One generic `PreloadToolHost<T>`
  + `PatchStrategy` trait, deduping `LiteinstGuest`/`E9patchGuest` (the 732/1218
  LOC near-duplicates) and the two `CoordinatorRpc` wrappers into one. liteinst
  runtime-patch = strategy #1. Pure refactor over Stage-1-proven capability;
  Mode B untouched.
- **Stage 3 — re-slot e9patch AOT rewrite as strategy #2** behind the same
  constructor; delete e9patch's duplicate ToolHost. e9patch stops being
  ptrace-preprocessing and becomes a real in-guest method. Compare AOT vs
  runtime-patch on measured (b)/coverage/fallback.
- **Stage 4 (separate, optional, do NOT gate 1-3 on it) — sabre.** Evaluate
  replacing sabre's adapter with the preload runtime, or keep sabre a distinct
  architecture. This is a rewrite-scale question.

## Bottom line

The shared abstraction the owner envisions is real and half-built for
liteinst+e9patch (the `SyscallDispatcher` seam + duplicated ToolHost prove it),
but the value is unlocked by **Stage 1**, not by the merge. Merging first would
unify inert paths and risk the flagship. sabre is not a third patching method —
it is a different architecture and belongs on its own track. Recommend adopting
the staged milestone with Stage 1 as a hard gate, and explicitly **not**
authorizing Stage 2+ until Stage 1's in-guest fast path is proven with the
(a)/(b) cost split.

---

## S1 reachability finding (2026-08-03, added after the verdict)

**S1 is BUILD-gated, not measurement-gated: full Detcore is not runnable on
liteinst Mode A today, so the (a)/(b) measurement cannot yet be taken.** This is
the pre-authorized "blocked S1 is a valid outcome" — but note it is blocked
*before* measurement, not by a measurement that shows no win. Three independent
lines of evidence at reverie `d2fb9a05`:

1. **Wiring.** The Mode A in-guest ToolHost entry (`install_tool`, exported as
   `n`/`n_from_bootstrap`) is instantiated only with **test tools** —
   `CounterTool`, `UnsubscribedLifecycleTool`, `InjectExitTool`
   (`reverie-liteinst/src/bin/rpc_tool_guest.rs`). It is **never** instantiated
   with `Detcore`. Hermit's only liteinst Detcore path is Mode B
   (`run_host_with_preload::<Detcore>`, hermit `hermit-cli/src/lib.rs:1528`).
   ⇒ Detcore has never run in-guest on liteinst.
2. **`Guest` contract stubs.** Mode A's `LiteinstGuest` returns
   `io::ErrorKind::Unsupported` for the scheduler's clock/timer:
   `set_timer` (`tool_host.rs:616`, *"LiteInst does not implement RCB timer
   delivery"*), `set_timer_precise` (`:624`), `read_clock` (`:632`, *"does not
   implement an RCB clock"*). No RCB clock + no timer ⇒ Detcore's deterministic
   virtual-time scheduler and preemption cannot run in-guest.
3. **Authoritative crate boundary.** `reverie-liteinst/CLAUDE.md`, *Supported
   Boundary*: *"Tool mode supports one process/thread. Timer and clock APIs, PMU
   preemption, guest callable signal handlers, exec bootstrap, and general
   clone/fork injection are not implemented."* This is the crate's own contract,
   not an inference.

**What DOES already exist in Mode A** (so the build is bounded, not open-ended):
the in-guest host *does* drive async tool futures to completion (`drive_ready`
spins on `Poll::Pending` with a no-op waker, `tool_host.rs:314-321`), and
`send_rpc` blocks on that spin (`:403-407`). So the **(a) sequentialization/park
mechanism exists in-guest today in a rudimentary busy-spin form** — a Detcore
handler that parks awaiting the global scheduler singleton would spin, not
deadlock. The missing pieces are specifically: the **RCB clock + timer +
in-guest PMU preemption delivery**, **multi-process/thread** support, the
**HybridPtrace lifecycle owner** (clone/fork/exec/vDSO/pre-exec), and finally
**wiring Detcore as the Mode A `T`**. That is the S1 build, in order.

### Salvageable clean (b) measurement — recommended immediate next datum

A real *instrumentation-cost-first* number is obtainable **without** building the
scheduler and **without touching Mode B or destabilizing the flagship**: measure
the **warm-patched in-guest hook round-trip** (a null/`CounterTool` tool, after
the first-trap patch is installed — signature `calls=N traps=1 hooks=N`) against
the **ptrace seccomp-stop round-trip** for the same syscall, per syscall, in a
**1-CPU box**. This is the cleanest possible isolation of axis **(b)**:
sequentialization **(a) = 0** (no scheduler, no global-state RPC on the hot path)
and instrumentation is a null tool, so the delta is purely trap-mechanism cost.
It directly answers the load-bearing unknown from Q4 — *can an in-guest patched
trap beat a ptrace stop on (b) at all?* — which bounds the entire perf case.
Parallelism/sequentialization cost is reported separately and is not part of this
datum. A null-tool (b) win is necessary-but-not-sufficient for the Detcore fast
path; a null-tool (b) *loss* would refute the perf hypothesis before any
scheduler work is spent, i.e. it could resolve S1 as NO cheaply.

**Recommendation:** treat S1 as opening with this (b)-isolation micro-benchmark
(needs a liteinst slot + a build; read-only w.r.t. Mode B), report the per-syscall
(b) delta first, and only authorize the full in-guest-scheduler build if (b)
shows a win worth pursuing.

# Shared in-guest Tool host (Family A) — TURNKEY BUILD SPEC

**Tasks:** `shared_inguest_toolhost_family` (BUILD), feeds `liteinst_flip_cli_to`,
`e9patch_hybridptrace_inguest_converge`, `unify_backend_stats_transport`, milestone
`unified-in-guest-patching-backend`.
**Author:** impl agent, opus-4.8, 2026-08-04 ~19:30Z. Built on the SCOPE-ONLY map
(`unified-in-guest-patching-backend-scope-20260804.md`), which is confirmed current against
reverie HEAD `04a46b43`.
**Design decision LOCKED by owner constraint (this spawn):** *"the Reverie tool lives ONLY in
the guest. Ptrace is an expensive hook; handler execution is always in-guest, global state in the
tracer via RPC. Shared = handler + host. Per-backend = only trap installation."* → **Path A**
(event-abstraction trait; regs stay per-backend inside `Guest<T>`). Path B (migrate LiteInst onto
the preload `SyscallEvent`, extend a shared public type) is REJECTED — it brushes a core-abstraction
change and is not needed (see §Crux).

---

## 0. The single subscriber's interface (scope answer, cited)

The subscriber ALREADY EXISTS and is backend-agnostic — nothing new to invent:
- `impl Tool for Detcore` — `hermit/detcore/src/lib.rs:780` (names no backend).
- Abstract contract: `Tool` `reverie/reverie/src/tool.rs:197` (`handle_syscall_event` `:333`),
  `GlobalTool` `:118`, `Guest<T>` `reverie/reverie/src/guest.rs:57`, `GlobalRPC<G>` `tool.rs:433`.
- Every backend hands `Detcore` in via `::<Detcore>`; `hermit-cli/src/lib.rs` is the only place
  that names/instantiates backends (`enum Backend :594`, dispatch `run_with_backend_inner :1491`).

Convergence is NOT a new interface — it is (a) collapsing the duplicated Family-A driver into one
shared host, and (b) flipping two backends' host wiring so `Detcore` runs in-guest.

---

## 1. Home of the shared host: `reverie-preload`

Both Family-A backends already depend on `reverie-preload` and its `dispatch` seam. The shared
driver belongs there (new module `reverie-preload/src/tool_host.rs`, exported from `lib.rs`).
`reverie-preload::dispatch` already owns: `SyscallEvent` (`dispatch.rs:38`), `SyscallDispatcher`
(`:160`), `is_fork_like` (`:172`), and `PassthroughDispatcher::apply_guards` (`:208`) — the
fail-closed policy both hosts re-implement (dedup target).

---

## 2. Crux — why Path A is clean (verified in code, not inferred)

Both dispatch loops touch the syscall event ONLY via `number`/`args`/`result`. Register access lives
ENTIRELY inside the per-backend `Guest<T>` impl:
- e9patch `E9patchGuest::regs()` → `aot::current_regs()` (a thread-local, NOT read from the event).
- liteinst `LiteinstGuest::regs()`/`set_regs()` → `HookContext*` in `crate::runtime::SyscallEvent.context`
  (`tool_host.rs:716-778`); the driver loop itself never mutates `event.number`/`event.args`.

The preload `SyscallEvent` exposes accessors only (`number()/args()/set_result()/fail()/source()`,
`dispatch.rs:83-118`) — no `number`/`args` setters, and the shared driver needs none. Therefore the
shared driver is generic over a small event trait; the concrete event stays per-backend.

---

## 3. Seam traits (Path A)

```rust
// reverie-preload/src/tool_host.rs

/// Read-surface the shared driver needs from a trapped syscall. Implemented by
/// BOTH concrete event types (reverie_preload::dispatch::SyscallEvent and
/// reverie-liteinst crate::runtime::SyscallEvent).
pub trait HostSyscallEvent {
    fn number(&self) -> i64;
    fn args(&self) -> [u64; 6];
    fn set_result(&mut self, result: i64);
    fn source(&self) -> SyscallEventSource; // SignalTrap | DirectInstrumentation
}

/// The per-backend seam. Everything NOT here is shared. The `Guest<T>` view and
/// trap/rewrite engine are the ONLY per-backend parts (== owner's "trap installation").
pub trait HostBackend<T: Tool> {
    type Event: HostSyscallEvent;
    type Guest<'a>: Guest<T> where Self: 'a;

    /// Build the per-thread handler view over this backend's concrete event.
    fn make_guest<'a>(&'a self, event: &'a mut Self::Event,
                      state: &'a mut T::ThreadState, ids: ThreadIds) -> Self::Guest<'a>;

    /// Backend fork support (liteinst: YES; e9patch: NO). Default = unsupported.
    fn supports_plain_fork(&self) -> bool { false }
    fn finish_fork_child(&self, /* ForkChildContext */) { /* per-backend, e9patch: unreachable */ }

    /// MANDATORY slowpath/fallthrough counter seam — NON-Option by design, see §5.
    fn slowpath_counter(&self) -> &dyn SlowpathCounter;
}
```

The driver (`drive_ready`, `drive_syscall`, `TailResult`/`TailAction`/`SyscallOutcome`, and the
**ERESTARTSYS/wait4 restartable-poll loop**) is hoisted verbatim from
`reverie-liteinst/src/tool_host.rs:491-568,313-370` — liteinst is a superset (adds `ForkChild`).
This is the concrete #362 requirement: the ERESTARTSYS retry (`tool_host.rs:319-330`) currently
lives ONLY in liteinst; hoisting it makes e9patch (`tool_host.rs:347-349`, today maps `err→-errno`
⇒ **app errno 512**) and any future in-guest backend inherit it.

---

## 4. What hoists vs. stays (verified)

**HOIST to `reverie-preload` (shared):**
- `SpinMutex`/`SpinGuard` — **byte-identical** in `reverie-liteinst/src/rpc.rs:18-67` and
  `reverie-e9patch/src/rpc.rs:16-67` (make `pub` in preload; both backends re-export). True dead-copy.
- Driver: `drive_ready`/`drive_syscall` + `TailResult`/`TailAction`/`SyscallOutcome` + ERESTARTSYS loop.
- Subscription filtering from `Tool::subscriptions`; one `ThreadState` map; first-poll no-op-waker driver.
- `is_exit_syscall`/`is_process_exit`/`raw_pid`/`fatal`/`tool_fatal` (bit-identical helpers).
- Reuse `PassthroughDispatcher::apply_guards` for the fail-closed guards both hosts re-implement.

**STAYS per-backend (== "only trap installation"):**
- The `Guest<T>` impl: regs/mem/stack + inject/tail_inject + trap→resume.
  liteinst `HookContext` trampoline vs e9patch `aot::current_regs` vs sabre `syscall_stackframe`.
- The trap/rewrite engine: liteinst SIGSYS+trampoline (`runtime.rs`), e9patch AOT rewrite
  (`rewrite.rs`), sabre C-ABI (`callbacks.rs`).
- The RPC WRAPPER: liteinst `CoordinatorRpc` has fork-reconnect (`rpc.rs:117-125`); e9patch does
  not. The driver is ALREADY generic over the RPC (`Guest` holds `&rpc`). **"One CoordinatorRpc"
  from scope §5.6 is NOT literal** — do not force e9patch to grow a fork path or liteinst to lose
  one. Hoist `SpinMutex`; keep two thin wrappers.
- Fork support (liteinst `finish_fork_child`/`is_plain_fork`; e9patch none).
- clock/timer (liteinst `read_clock→0`/`set_timer` no-op `tool_host.rs:887-903`; e9patch Unsupported).

---

## 5. THE PROPERTY THAT MUST SURVIVE — slowpath/fallthrough counts

**Why (owner):** LiteInst's "14.5x slower" was a retired legacy path nothing distinguished from a
slow backend. A shared host WITHOUT the per-path counter hides that failure across THREE backends
at once. See `[[slowpath-counter-is-liteinst-not-e9patch]]`, `[[silent-fastpath-fallback-needs-observable-signal]]`.

**Mechanism:** `HostBackend::slowpath_counter()` returns `&dyn SlowpathCounter` — **NON-Option**.
A converging backend cannot compile without providing one ⇒ it cannot silently drop the counter.

**Taxonomy to expose per-backend** (from `reverie-liteinst/src/stats.rs:111`, CONFIRMED for
hermit-liteinst on `unify_backend_stats_transport`):
- `direct_hook` = **FASTPATH**
- `first_site_seccomp`, `ptrace_installation`, `unpatchable_or_other`, `in_guest_sigsys`,
  `in_guest_nested_sigsys`, `cacheline_straddler` = **SLOWPATH** classes

Counts are PRODUCED at trap-install time (per-backend, inside the trap engine — each engine knows
fast vs slow). The shared host owns SUBMISSION at process exit
(`reverie-liteinst/src/tool_host.rs:456-460` `runtime::submit_process_stats`) — hoist the submission
call into the shared exit path so every backend submits identically.

**Vacuity guard that already works** (keep it): `direct_hook == N+1 && ptrace_installation == 0`
detects silent fallthrough (`slowpath-counter` memory).

---

## 6. Stats transport = PART of the shared host (owner constraint 3; resolves `unify_backend_stats_transport`)

The stats transport is NOT a fourth parallel path. Resolution:
- **Family A (liteinst + e9patch):** ONE producer surface = the RPC `GlobalTool` shape
  (`LiteinstStatsGlobal` on `stats.sock`, `reverie-liteinst/src/stats.rs:396`) generalized to a
  backend-agnostic in-guest stats `GlobalTool`, submitted BY THE SHARED HOST via the mandatory
  counter seam (§5). No second transport is built inside the shared host.
- **Family B (SaBRe):** keeps its shmem-memfd engine (`reverie-sabre-stats/src/lib.rs:121,221`) — a
  DIFFERENT engine, not a fourth path in the shared host. It CONFORMS to the SAME `BackendStatsSource`
  contract (`reverie/reverie/src/backend_stats.rs`) and the SAME taxonomy (§5).
- **Unify behind the ONE contract `BackendStatsSource`**, not behind one wire transport. Both
  surfaces already funnel through it; the taxonomy is the shared invariant.

---

## 7. Ordered build increments (each independently compilable + testable)

1. **Hoist `SpinMutex`/`SpinGuard` → `reverie-preload` (pub).** Both `rpc.rs` re-export. Smallest,
   zero-behavior-change, validates the cross-crate plumbing. (liteinst tests: `rpc_tool.rs`, `strace.rs`.)
2. **Add `reverie-preload::tool_host`**: `HostSyscallEvent`, `HostBackend`, `SlowpathCounter`, and the
   generic driver + ERESTARTSYS loop (hoisted from liteinst). Not yet wired ⇒ gate with a unit test
   to keep clippy `--all-targets` green (no dead code).
3. **Wire LiteInst onto the shared driver** (lower risk: host exists + tested + already has ERESTARTSYS).
   Delete liteinst's duplicated driver; `ToolHost`/`LiteinstGuest` implement the seam. Validate:
   `cargo test -p reverie-liteinst --all-features -- --test-threads=1` (esp. `rpc_tool.rs`) + `-p reverie-examples --test liteinst`.
4. **Wire e9patch onto the shared driver** — it inherits ERESTARTSYS (kills app errno 512). e9patch is
   still gated by `install_hybrid_runtime → Unsupported` (`runtime.rs:259-262`); the HybridPtrace
   lifecycle owner is the SEPARATE `e9patch_hybridptrace_inguest_converge` task.
5. **Fold stats submission** (§6) into the shared exit path.

**STOP-ORDER honored:** compat lifts only as each backend ACTUALLY converges + the all-6-backend lint
(#1571 `83d0bf34`, landed) stays in force — not because a decision task closed.

---

## 8. Validation gate (reverie)

`./validate.sh` (workspace, all-features: build + test + doc-test + clippy + rustfmt). Focused during
iteration: `cargo test -p reverie-liteinst --test rpc_tool -- --test-threads=1`. Reverie-only is L0;
an integrated determinism claim needs the landed Hermit CLI path (see `liteinst_flip_cli_to`).
Run `validate.sh` via `systemd-run --user` (agent sandbox cannot self-cgroup) — see parent policy.

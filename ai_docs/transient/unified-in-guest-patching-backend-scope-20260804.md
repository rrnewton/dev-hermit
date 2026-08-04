# Unified in-guest patching backend — SCOPE MAP (shared vs duplicated)

**Task:** `unified-in-guest-patching-backend` (milestone). **Mode: SCOPE-ONLY (do not build).**
**Author:** impl agent, opus-4.8, 2026-08-04. **Method:** 4 read-only cross-backend code-search
passes (liteinst / e9patch / sabre / shared trait surface), 3 load-bearing claims re-verified by
direct Read. Repos: `~/work/dev-hermit/{reverie,hermit}`. reverie HEAD `04a46b43`, hermit on main.

## The decision this scopes (from the closed decision task)

**ONE in-guest Detcore subscriber. Only PATCHING MECHANICS stay per-backend.** Tool + per-thread
ThreadState live ONLY in the guest; ptrace is a lifecycle-only expensive hook (fast path: patcher
catches instruction → in-guest handler; slow path: ptrace traps → tracer sets guest RIP to the
in-guest handler → continues); global state stays in the tracer, reached by RPC.

## Headline finding

**The single subscriber ALREADY EXISTS and is already backend-agnostic.** `Detcore` implements the
abstract Reverie `Tool` contract and names no backend; every backend hands it in via `::<Detcore>`.
The convergence work is **NOT** defining a new interface — it is **collapsing three duplicated
in-guest "Tool host" drivers into one** and **flipping two backends' host wiring** so Detcore
actually runs in-guest. The interface below is the contract that one shared host must satisfy.

---

## 1. What is GENUINELY SHARED today (do not rebuild)

| Shared asset | File(s) | Role |
|---|---|---|
| Abstract `Tool` trait | `reverie/reverie/src/tool.rs:197` (`handle_syscall_event` `:333`) | The subscriber contract itself |
| Abstract `GlobalTool` | `reverie/reverie/src/tool.rs:118` (`receive_rpc` `:141`) | Cross-process global state contract |
| Abstract `Guest<T>` | `reverie/reverie/src/guest.rs:57` (regs/mem/inject/tail_inject; `inject_with_retry` `:212` already retries EINTR/ERESTARTSYS) | Per-thread handler view a host must supply |
| `GlobalRPC<G>` | `reverie/reverie/src/tool.rs:433` | guest→global channel (supertrait of Guest) |
| **The subscriber** `impl Tool for Detcore<T>` | `hermit/detcore/src/lib.rs:780`; ThreadState `hermit/detcore/src/tool_local.rs:1195`; GlobalState `hermit/detcore/src/tool_global.rs:260` | Already the single shared Tool; depends only on abstract reverie |
| RPC transport (guest↔tracer global state) | `reverie/reverie-rpc-transport/src/{server.rs:39,envelope.rs:26}` | `RpcServer`, `RequestEnvelope{from,request}`, `BlockingRpcClient` |
| **`reverie-preload`** LD_PRELOAD + seccomp/SIGSYS runtime | `reverie-preload` (`SyscallDispatcher` trait, `SyscallEvent`, `install()`) | The generic lower seam both in-process backends already build on |
| `BackendStatsSource`/`BackendStatsSnapshot` | `reverie/reverie/src/backend_stats.rs:{26,58,64}` | Shared stats contract (transport differs — see §4) |

hermit-cli is the only place that names backends and instantiates them (abstraction boundary holds):
`hermit/hermit-cli/src/lib.rs` — enum `Backend` `:594`; dispatch `run_with_backend_inner` `:1491`.

---

## 2. What is DUPLICATED — the actual convergence surface

There are **three** separate in-guest "Tool host" drivers. They fall into **two families**:

### Family A — reverie_preload / SIGSYS (liteinst + e9patch): near-identical `ToolHost<T>`, duplicated

| Concern | LiteInst | e9patch |
|---|---|---|
| Tool host struct | `reverie-liteinst/src/tool_host.rs:192` `ToolHost<T>{ tool, rpc, root_pid, subscriptions, states, stats }` | `reverie-e9patch/src/tool_host.rs:160` `ToolHost<T>{ tool, rpc, root_pid, subscriptions, states, … }` |
| install entry | `install_tool` `:83` (+quiescent `:107`, from_bootstrap `:129`) | `install_tool` `:89` → `reverie_preload::install(...)` `:152` |
| dispatch seam | crate-local `trait ToolHandler` `:67` over `crate::runtime::SyscallEvent` | `impl SyscallDispatcher for ToolHost<T>` `:227` — the **reverie_preload** seam |
| syscall-event driver | `dispatch` `:205`, loop `:313-370`; `drive_ready` `:491`/`drive_syscall` `:534`; `SyscallOutcome`/`TailAction`/`TailResult` `:506/:520/:570` | `dispatch` `:231`; driven `:343` — same shape, re-implemented |
| `Guest<T>` impl | `LiteinstGuest` `:620`, `impl Guest<T>` `:688` | `E9patchGuest` `:505`, `impl Guest<T>` `:530` |
| RPC client | `reverie-liteinst/src/rpc.rs:81` `CoordinatorRpc<G>` | `reverie-e9patch/src/rpc.rs:72` `CoordinatorRpc<G>` (duplicate) |

The **only genuinely different parts** are inside the `Guest<T>` impl: register/memory/stack access
and trap→resume plumbing (LiteInst `liteinst2::trampoline::HookContext` vs e9patch `aot::current_regs`).
Everything else in Family A is duplicated and hoistable.

### Family B — SaBRe native C-ABI plugin (the CONFORMING reference, different shape)

SaBRe has no top-level crate; it is `reverie/experimental/reverie-sabre*` (4 crates) + `hermit/detcore-sabre`.
It does **not** use reverie_preload/SIGSYS — its engine rewrites call sites to a C-ABI entry in-process.

- Adapter (native `reverie_sabre::Tool` → shared `reverie::Tool`): `reverie/experimental/reverie-sabre/src/reverie_adapter.rs` — `ReverieAdapter<T>` `:77`, `RemoteReverieAdapter<T>` `:342`; dispatch `dispatch_syscall` `:128`; `SabreGuest` (`impl Guest<T>`) `:991/:1089`.
- Detcore host: `hermit/detcore-sabre/src/lib.rs` — `Plugin{ adapter: RemoteReverieAdapter<Detcore> }` `:190`, `#[sabre::tool] impl reverie_sabre::Tool for Plugin` `:240`.
- Rewrite mechanics (SaBRe-specific): `reverie-sabre/src/callbacks.rs` (C-ABI entry `handle_syscall` `:497`), `tool.rs`, `thread.rs`.

**Implication:** a single shared *driver* can cover Family A directly. Family B keeps its own
adapter because its trap mechanism is a different (C-ABI, no-SIGSYS) engine — but it must present the
**same `Guest<T>` surface and the same result-dispatch semantics** (§5). "One subscriber" is
satisfied at the `Tool`/`Guest<T>` boundary; "one driver" is realistic only for Family A.

---

## 3. State duplication (guest vs host), with paths

**Per-thread `ThreadState` map — in-guest in all three, three separate definitions:**
- LiteInst: `ToolHost.states: SpinMutex<HashMap<i32, T::ThreadState>>` — `reverie-liteinst/src/tool_host.rs:192-199`
- e9patch: `ToolHost.states: HashMap<i32, T::ThreadState>` — `reverie-e9patch/src/tool_host.rs:160-168`
- SaBRe: `ReverieAdapter.thread_states: Mutex<HashMap<i32, ThreadStateCell<T>>>` `:84`; remote `:342-357`

**Tool instance — in-guest in all three:** LiteInst `ToolHost.tool: SpinMutex<Option<T>>`; e9patch `ToolHost.tool: T`; SaBRe `ReverieAdapter.tool` (+ in-process `global_state`) or `RemoteReverieAdapter` (RPC).

**Global state — in the tracer/coordinator, reached by RPC (the target shape):** Detcore `GlobalState`
singleton `hermit/detcore/src/tool_global.rs:260` ("lives inside the tracer"); server `reverie-rpc-transport/src/server.rs:39`; clients: `CoordinatorRpc<G>` duplicated in liteinst & e9patch `rpc.rs`, SaBRe uses `SabreRpc`/`RemoteThreadState.rpc` (`reverie_adapter.rs:964/358`).

**Stats-without-the-Tool — duplicated AND two different transports (premise correction):**
- LiteInst: a **separate RPC GlobalTool** `LiteinstStatsGlobal` on its own `stats.sock` — `reverie-liteinst/src/stats.rs:396` (server bound `backend.rs:711-719`).
- SaBRe: a **shared-memory memfd page** `SabreStats`/`RawBackendStats` — `reverie/experimental/reverie-sabre-stats/src/lib.rs:121,221` (NOT RPC).
- Both surface through the shared `BackendStatsSource` contract. The task's phrasing ("distinct typed
  RPC GlobalTool that aggregates counters without instantiating the Tool") matches **LiteInst's** shape;
  SaBRe's is shmem. Converging the stats transport is a sub-decision, not a blocker.

---

## 4. Per-backend conformance TODAY (verified against running code, not the claim)

| Backend | Status | Evidence |
|---|---|---|
| **SaBRe** | **CONFORMS** | Tool+ThreadState in-guest (`reverie_adapter.rs`), ptrace lifecycle-only: `SabreGuest::set_regs` refuses RIP/RSP rewrites with `EOPNOTSUPP` `:1171`, memory via `process_vm_readv/writev` not `PTRACE_PEEK`. hermit `run_sabre` builds GlobalState + RpcServer then `sabre_ptrace::run` (`lib.rs:994`). |
| **LiteInst** | **In-guest host exists & is tested, but hermit ships HOST-HYBRID** | In-guest `tool_host.rs` complete (exercised by `reverie-liteinst/tests/rpc_tool.rs`) and already carries the #362 ERESTARTSYS fix (`:319-330`). BUT hermit-cli calls `run_host_with_preload::<Detcore>` — Tool driven from the ptrace **host** — `hermit/hermit-cli/src/lib.rs:1531-1546` (**verified**). In-guest `run_with_preload*` (`backend.rs:362`) has **no hermit caller**. |
| **e9patch** | **In-guest path DORMANT; ships as ptrace preprocessing** | `runtime_backend()` hard-downgrades `E9patch → Ptrace` — `hermit/.../run.rs:1714-1720` (**verified**); Detcore then runs 100% host-side under `TracerBuilder::<Detcore>`. The in-guest direct-AOT path exists (`aot.rs`+`tool_host.rs`+`rpc.rs`) but `install_hybrid_runtime` returns `io::ErrorKind::Unsupported` — `reverie-e9patch/src/runtime.rs:259-262` (**verified**), awaiting the `HybridPtrace` lifecycle owner. e9patch is correctly "not a backend" today (preprocessing + ptrace). |

---

## 5. The single subscriber's interface (the contract convergence must hit)

The subscriber = the existing abstract contract; nothing new to invent:
`Tool` (`tool.rs:197`) + `GlobalTool` (`tool.rs:118`) + `Guest<T>`/`GlobalRPC` (`guest.rs:57`, `tool.rs:433`),
implemented by `Detcore` (`detcore/src/lib.rs:780`).

**What ONE shared in-guest host (Family A) must own (hoist from the two duplicates):**
1. Sit on the **`reverie_preload::SyscallDispatcher`/`SyscallEvent`** seam (pick this over LiteInst's crate-local `ToolHandler`).
2. Subscription filtering from `Tool::subscriptions`.
3. Per-thread `ThreadState` map (one definition).
4. The synchronous first-poll future driver (`drive_ready`/`drive_syscall`, no-op waker; `SyscallOutcome`/`TailAction`/`TailResult`).
5. **The syscall-result mapping loop INCLUDING the ERESTARTSYS/`wait4` restartable-poll retry** — currently ONLY in LiteInst (`tool_host.rs:319-330`); e9patch (`tool_host.rs:347-349`) lacks it and would return application errno 512. This is the concrete #362 requirement for ANY in-guest host and MUST live in the shared driver so e9patch (and SaBRe) inherit it.
6. One `CoordinatorRpc<G>` over `reverie-rpc-transport` (replace the two copies).

**What stays PER-BACKEND (the patching MECHANICS = the `Guest<T>` impl + the trap engine):**
- register/memory/stack access + inject/tail_inject + trap→resume: LiteInst `HookContext` trampoline vs e9patch `aot::current_regs` vs SaBRe `syscall_stackframe`.
- the rewrite/trap-install engine itself: LiteInst SIGSYS+trampoline (`runtime.rs`), e9patch AOT ELF rewrite (`rewrite.rs`), SaBRe C-ABI rewrite (`callbacks.rs`).

---

## 6. Convergence lift, ordered by size (for the execution plan — NOT built here)

1. **PREREQ #0 — DONE.** Backend-abstraction lint now covers all 6 backends (PR #1571 merge `83d0bf34`, ancestor of main; coordinator-verified 2026-08-04 17:47Z).
2. **Hoist Family A into one shared host** (over `reverie_preload` seam) incl. the ERESTARTSYS retry (§5.5) and one `CoordinatorRpc`. Medium.
3. **LiteInst: flip hermit-cli** from `run_host_with_preload::<Detcore>` to the in-guest `run_with_preload::<Detcore>` path; finish in-guest RCB/timer support (currently `read_clock`→0, `set_timer` no-op, `tool_host.rs:887-903`). Small-medium (host already exists+tested).
4. **e9patch: largest lift** — implement the `HybridPtrace` lifecycle owner so `install_hybrid_runtime` stops returning `Unsupported`, add the ERESTARTSYS arm, then remove/redefine the `E9patch→Ptrace` downgrade in `runtime_backend()`. Large.
5. **Stats transport sub-decision:** unify LiteInst RPC-GlobalTool vs SaBRe shmem-memfd behind one `BackendStatsSource` producer, or keep both and document. Small.

**STOP-ORDER (per e9patch's own words) remains honored:** the three-lanes compat stop-order lifts
only as each backend ACTUALLY converges + lint lands — not because the decision task closed. This is
architecture scoping only; no compat expansion performed.

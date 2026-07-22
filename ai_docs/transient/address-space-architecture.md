# Reverie tool address-space architecture (per backend)

Status: research snapshot / point-in-time analysis. Author: agent `hermit-061`
(task `vision-reverie-address-spaces`). Read-only survey; no code changed.

Grounded in the checkouts under `~/work/dev-hermit` on 2026-07-22:
`reverie/` (primary, workspace incl. `experimental/`), the `reverie-dbi`
DynamoRIO crate in the `reverie/reverie-gdb-finish-fix/` worktree (pinned rev
`e3e2c965`), and `hermit/` (detcore + hermit-cli). File:line references are to
those trees.

Companion docs that this one ties together:
`ai_docs/transient/20260722_dbi-reverie-interface-gap.md` (DBI gap analysis),
`ai_docs/kvm_backend_design.md` (KVM gVisor proposal),
`ai_docs/sabre_backend_assessment.md` / `ai_docs/sabre-determinism-analysis.md`.

---

## TL;DR

- **The Reverie *core traits* were designed for separate address spaces from
  the start.** `GlobalTool::receive_rpc` is documented as "intended to be IPC …
  in some backends, and a local method call in others"; `Tool::ThreadState`
  must be `Serialize + DeserializeOwned` precisely because it "may have to be
  migrated between address spaces by a Reverie backend"; `GlobalRPC` is "a
  handle to send messages to … (potentially a remote, inter-process
  communication)". The single-vs-multiple address-space choice is a **backend**
  decision, not a tool or core-trait decision.
- **The user's "tool `.so` in the guest + RPC to global state" vision already
  exists** — as `experimental/reverie-sabre` + `experimental/reverie-rpc` +
  `experimental/reverie-host`, demoed by `experimental/riptrace`. It injects a
  `cdylib` tool into the guest and talks to an out-of-process global state over
  a Unix socket. **Caveat:** it uses a *separate, synchronous* `reverie_sabre::Tool`
  API, not the shared async `reverie::Tool`.
- **There are two different "DBI" efforts, do not conflate them:**
  - `reverie-sabre` (SaBRe selective binary rewriting) — true **multi**
    address space, separate tool API. Runnable demo.
  - `reverie-dbi` (DynamoRIO) — adapts the **shared** async `reverie::Tool`,
    but its RPC is an in-process shortcut, so it is **single** address space
    today, and it only hosts a hard-coded `PrototypeTool`, not Detcore.
- **KVM is two things:** a design proposal (gVisor Sentry bridge; tool stays
  out-of-process in a Rust process — **multi** address space) and an actual
  minimal `reverie-kvm` crate (single-vCPU `vmcall` syscall-transport
  prototype; **not** yet a Linux execution backend).
- **Detcore itself is backend-agnostic and unchanged by all of this.** It is a
  `reverie::Tool`/`GlobalTool` pair depending only on the abstract `reverie`
  crate. Today only the ptrace backend actually launches it. Its `GlobalState`
  is *interface*-ready for cross-address-space RPC (serializable
  `Request`/`Response`) but its *implementation* still assumes a single shared
  address space (`Arc<Mutex<…>>`, a local tokio scheduler task, TODOs to move
  the vector clock into shared memory).

---

## 1. The core contract: designed for N address spaces

The `reverie` crate defines three traits and no explicit `Backend` trait — the
backend contract is *implicit* (see the DBI gap doc for the full method list).
The three traits (`reverie/reverie/src/tool.rs`, `reverie/reverie/src/guest.rs`):

- **`GlobalTool`** — the singleton global half. One instance for the whole
  process tree.
  - `type Request/Response/Config: Serialize + DeserializeOwned` (`tool.rs:41-48`).
  - `async fn receive_rpc(&self, from: Tid, msg) -> Response` (`tool.rs:62`)
    — doc: *"intended to be IPC, inter-process communication, in some backends,
    and a local method call in others, but never truly a communication between
    different machines."*
- **`Tool`** — the per-process local half; a factory for per-thread state.
  - `type GlobalState: GlobalTool` — links the two halves into one tool spec.
  - `type ThreadState: Serialize + DeserializeOwned` (`tool.rs:137`) — doc:
    *"Both thread-local and process-local state may have to be migrated between
    address spaces by a Reverie backend."*
  - Async handlers (`handle_syscall_event`, `handle_signal_event`,
    `handle_timer_event`, lifecycle) that receive `&mut impl Guest<Self>` and
    **may suspend**.
- **`GlobalRPC<G>`** (`tool.rs:333-341`) — the transport handle a `Guest` must
  provide: `async fn send_rpc(&self, G::Request) -> G::Response` + `config()`.
  Doc: *"a handle to send messages to the global state (potentially a remote,
  inter-process communication)."*

The serialization bounds only make sense if a backend can put the tool halves
in different address spaces. **This is the design intent the task refers to.**

### Even the centralized backend exercises the boundary

`reverie-ptrace` is centralized (one tracer process owns the single
`GlobalState`). Yet `WrappedFrom::send_rpc` deliberately **round-trips the RPC
payload through `bincode` even though the call is in-process**
(`reverie-ptrace/src/task.rs:2258-2273`):

```rust
struct WrappedFrom<'a, G: GlobalTool>(Tid, &'a GlobalState<G>);
impl<'a, G: GlobalTool> GlobalRPC<G> for WrappedFrom<'a, G> {
    async fn send_rpc(&self, args: G::Request) -> G::Response {
        let serial = bincode::serde::encode_to_vec(&args, ..).unwrap();   // encode
        let deserial = bincode::serde::decode_from_slice(&serial, ..)..;  // decode
        self.1.gs_ref.receive_rpc(self.0, deserial).await                 // then local call
    }
}
```

This keeps tools honest about the serialization contract so the *same* tool can
later run on a cross-address-space backend without changes.

---

## 2. The RPC / global-state infrastructure

Two distinct RPC stacks exist, matching the two tool APIs.

### 2a. Shared-trait RPC (used by ptrace and reverie-dbi)

There is no separate socket layer; `GlobalRPC` is implemented directly by the
per-thread guest handle and dispatches to `GlobalState::receive_rpc`:

- ptrace: `TracedTask<L>: GlobalRPC` (`task.rs:2242`) → `WrappedFrom` → bincode
  round-trip → local `receive_rpc` (single process).
- reverie-dbi: `DbiGuest<T>: GlobalRPC` (`reverie-dbi/src/lib.rs:104-118`) →
  `self.global_state.receive_rpc(self.tid, message).await` — a **direct
  in-process call, no serialization, no socket**. Works only for a global that
  is in the same address space (today `GlobalState = ()`).

### 2b. Cross-process RPC infra (used by reverie-sabre) — `experimental/`

This is the purpose-built multi-address-space stack:

- **`reverie-rpc`** (`experimental/reverie-rpc/`): the wire protocol shared by
  guest and host.
  - `Channel<Req,Res>` (`channel.rs`): `send(&Req)` (fire-and-forget) and
    `call(&Req) -> Res` (request/response). `MakeClient` builds a typed client
    from a `BoxChannel`.
  - `Service` (`service.rs`): server side; `call(Request) -> Option<Response>`
    (`None` = send-only message, no reply).
  - `#[reverie_rpc::service]` proc-macro (`reverie-rpc-macros/`) generates, from
    a trait: the server trait + `.serve()`, the `Request`/`Response` enums, and
    a typed **`…Client`** that turns method calls into `channel.send`/`.call`.
    `#[rpc(no_response)]` marks send-only methods.
- **`reverie-host`** (`experimental/reverie-host/`): the host / global-state
  side. `Server` (`server.rs`) binds a Unix `UnixListener` in `$XDG_RUNTIME_DIR`
  (or `/tmp`), `accept()`s guest connections, and for each connection loops
  `codec::read → service.call → codec::write` (skipping the write for send-only
  requests). Also provides `TracerBuilder`/child launch for in-guest backends.
- **guest side** (`reverie-sabre/src/rpc.rs`): `BaseChannel` connects to the
  server via `$REVERIE_SOCK`, `dup3`s the socket onto **fd 100** (`SOCKET_FD`,
  chosen to stay clear of early fds and to be debuggable), marks it CLOEXEC and
  "protected", and implements `Channel::{send,call}` with length-prefixed
  `encode`/`decode`. The socket lives inside the guest process; the global
  state lives in the host process.

```
guest process (tool .so)                       host process (global state)
  MyServiceClient  --.send()/.call()-->  BaseChannel(fd 100)
        │                                       │  UnixStream  (REVERIE_SOCK)
        │                                       ▼
        └──────────── serialized Request ───────────────►  Server.accept()
                     ◄─────────── Response ─────────────   service.call(req)
                                                             (S: reverie_rpc::Service)
```

---

## 3. Per-backend address-space models

Legend: `┃` = address-space (process/protection) boundary that RPC/serialization
must cross.

### 3a. ptrace (production; `reverie-ptrace`) — CENTRALIZED, single tool space

One tracer process holds the tool, all per-thread state, and the single
`GlobalState` on a tokio `LocalSet`; guests are ordinary separate processes but
run **no tool code** — the tracer drives them via ptrace/seccomp across the
process boundary. Tool ↔ global is same-address-space (with a bincode
round-trip for contract fidelity).

```
        TRACER PROCESS (trusted)                 ┃   GUEST PROCESS(es) (untrusted)
 ┌───────────────────────────────────────────┐  ┃  ┌───────────────────────────┐
 │ Tracer<G>  (LocalSet, single thread)       │  ┃  │  application code          │
 │  ├─ Tool: Detcore  (per-process state)     │  ┃  │  (no tool code here)       │
 │  ├─ per-thread ThreadState                 │  ┃  │                            │
 │  └─ Arc<GlobalState>  (the one global)     │  ┃  │  syscalls / signals        │
 │        ▲   GlobalRPC = WrappedFrom          │  ┃  │        │                   │
 │        │   (bincode round-trip, local)      │  ┃  │        ▼ ptrace-stop       │
 │  TracedTask<L>: Guest<L> ───ptrace/seccomp──╂──┃──►  PTRACE_* / PTRACE_SYSCALL │
 └───────────────────────────────────────────┘  ┃  └───────────────────────────┘
   spawn().wait() -> (ExitStatus, GlobalState)   ┃
```

- Tool/global address spaces: **one** (both in the tracer).
- Boundary crossed: tracer↔guest via ptrace (memory/regs are remote reads).
- `Guest::memory` = remote process memory; `regs`, `inject`, `tail_inject`,
  timers (RCB/PMU), signals all implemented. This is the reference backend.

### 3b. SaBRe DBI (`experimental/reverie-sabre` + `riptrace`) — SEPARATE tool space (the vision)

The tool is compiled to a `cdylib` (`libriptrace_plugin.so`,
`crate-type = ["cdylib","rlib"]`) and **loaded into the guest process** by the
SaBRe loader (entry `sbr_init<T>`, `reverie-sabre/src/internal.rs:42`). SaBRe
selectively rewrites syscall sites; the intercepted syscall jumps to the tool's
**synchronous** `syscall()` callback, which runs *in the guest address space*
with direct `LocalMemory` access. Global state is a separate host process
reached over the socket RPC of §2b.

```
   GUEST PROCESS (untrusted)                     ┃   HOST PROCESS (trusted)
 ┌──────────────────────────────────────────┐   ┃  ┌──────────────────────────────┐
 │ application code (rewritten by SaBRe)     │   ┃  │ riptrace (host bin)          │
 │   │ syscall site                          │   ┃  │  ├─ GlobalState (Arc)         │
 │   ▼                                       │   ┃  │  └─ reverie_host::Server      │
 │ libriptrace_plugin.so  (the TOOL)         │   ┃  │        S: reverie_rpc::Service│
 │  ├─ reverie_sabre::Tool::syscall(&self,   │   ┃  │           ▲                   │
 │  │     Syscall, &LocalMemory)  [sync]     │   ┃  │           │                   │
 │  ├─ LocalMemory (direct, in-process)      │   ┃  │           │                   │
 │  └─ MyServiceClient ─ BaseChannel(fd 100) ─╂───┃───────────►┘  UnixListener      │
 │        (send/call, bincode)                │   ┃  │        ($REVERIE_SOCK)        │
 └──────────────────────────────────────────┘   ┃  └──────────────────────────────┘
```

- Tool/global address spaces: **two** (tool in guest, global in host).
- Boundary crossed: guest tool ↔ host global via Unix-socket RPC (serialized).
- `Guest::memory` = direct `LocalMemory` (no remote read needed — the tool *is*
  in the guest).
- **This is exactly the user's DBI vision.** Caveats (from
  `reverie-sabre/{ASSESSMENT,CAPABILITIES}.md`): separate *synchronous*
  `reverie_sabre::Tool` (not the shared async `reverie::Tool`), so ptrace tools
  cannot switch to it by recompiling; no register/stack/inject/timer/subscription
  parity; x86-64 dynamically-linked guests only; RPC is blocking and reserves
  fd 100; the SaBRe loader is built/pinned separately
  (`SABRE_UPSTREAM.toml`). Status: builds, 17 unit tests pass, runs the strace
  demo and a conformance gate; **not** a production isolation boundary yet.

### 3c. DynamoRIO DBI (`reverie-dbi`, rev e3e2c965) — SHARED trait, but SINGLE space today

Unlike SaBRe, `reverie-dbi` adapts the **shared async `reverie::Tool`**:
`DbiGuest<'a, T: Tool>` is generic and implements `Guest<T>` and `GlobalRPC`
(`reverie-dbi/src/lib.rs:55-118`). The DynamoRIO native client rewrites the hot
path and, on each syscall, constructs a `DbiGuest` and dispatches a tool
handler — but:

- The C ABI binds a concrete `static PrototypeTool` with `GlobalState = ()`
  (not Detcore); `run_ready` polls the handler future **once** and panics on
  `Poll::Pending` — no executor, handlers may not suspend.
- `GlobalRPC::send_rpc` is the in-process shortcut
  `self.global_state.receive_rpc(...)` — **no serialization, no socket**, so
  "global state" only works when it is `()` in the same address space.
- Hermit's `--backend dbi` (`hermit-cli/src/bin/hermit/backends.rs::run_dbi`,
  `run.rs:754-758`) shells out to DynamoRIO `drrun -c <client.so>` and returns
  only an OS exit code.

```
        drrun / DynamoRIO (single process)                     ┃  (no separate host)
 ┌─────────────────────────────────────────────────────────┐  ┃
 │ guest application (JIT-rewritten in place by DynamoRIO)  │  ┃  Detcore's GlobalState
 │   │ syscall                                              │  ┃  would need a host +
 │   ▼                                                      │  ┃  socket RPC to live
 │ libreverie_dbi_client.so                                 │  ┃  across a boundary —
 │   ├─ static PrototypeTool (GlobalState = ())             │  ┃  NOT built yet.
 │   ├─ DbiGuest<PrototypeTool>: Guest + GlobalRPC          │  ┃
 │   │     send_rpc = global_state.receive_rpc() [in-proc]  │  ┃
 │   └─ run_ready: poll once, panic on Pending              │  ┃
 └─────────────────────────────────────────────────────────┘  ┃
```

- Tool/global address spaces: **one** today (in-process, `()` global).
- To host Detcore it needs: a `DbiTracer<T>`/builder, a real (LocalSet)
  executor that permits suspension, and a **cross-process `GlobalRPC`** (socket
  / shared memory + serialization) so the single Detcore `GlobalState` is
  reachable from each DynamoRIO-instrumented guest. See the DBI gap doc for the
  full 8-item gap list and phased roadmap. In other words, DynamoRIO's *natural*
  model is the multi-address-space one (like SaBRe), but the current adapter
  took the in-process shortcut.

### 3d. KVM — proposal (multi-space) vs. current crate (transport prototype)

Two separate artifacts:

**(i) `ai_docs/kvm_backend_design.md` — the proposed backend (multi space).**
Reuse gVisor's Sentry+KVM as the Linux engine; run existing `reverie::Tool`s in
a separate `reverie-kvm` **Rust process**; a narrow Sentry bridge publishes
syscall/lifecycle events over sealed memfd rings + eventfds; `KvmGuest<T>`
proxies memory/regs/inject over the bridge. `GlobalTool` lives once in the Rust
process (`GlobalRPC::send_rpc` = local async call).

```
   reverie-kvm RUST PROCESS (trusted tool)   ┃  runsc/Sentry sandbox      ┃  guest app
 ┌──────────────────────────────────────┐    ┃ ┌──────────────────────┐   ┃ ┌──────────┐
 │ TracerBuilder<T>/Tracer<G>            │    ┃ │ Reverie bridge patch │   ┃ │ ring 3   │
 │  ├─ Tool + GlobalState (in Rust)      │    ┃ │ gVisor task/syscall/ │   ┃ │ SYSCALL  │
 │  └─ KvmGuest<T>: Guest<T>  ───bridge──╂────┃─┤ signal impl          ├───╂─► (Sentry  │
 │        memfd rings + eventfds         │    ┃ │ KVM platform, gofer  │   ┃ │  ring 0) │
 └──────────────────────────────────────┘    ┃ └──────────┬───────────┘   ┃ └──────────┘
                                              ┃         /dev/kvm           ┃
```
  - Tool/global address spaces: **two+** (tool/global in Rust; Linux tasks in
    Sentry; app behind KVM). Est. 6–9 months to an x86-64 MVP (see doc).

**(ii) `reverie/reverie-kvm/` — the crate that exists today (NOT a tool host).**
A "minimal x86-64 KVM primitives" prototype: `KvmBackend` creates a VM with one
**real-mode vCPU** and one memory slot, and turns a guest `vmcall`/`vmmcall`
(opcode `VMCALL_SYSCALL_TRANSPORT = 12`) into a host-side `SyscallRequest`
carrying a syscall number + 6 args in a guest-memory frame
(`reverie-kvm/src/vm.rs`). Its README is explicit: *"not yet a Linux execution
backend … KVM does not provide Linux syscall semantics, process lifecycle,
virtual memory, signals, or filesystem behavior."* It implements **none** of the
`reverie::Tool`/`Guest` contract yet. Hermit's `--backend kvm`
(`backends.rs::run_kvm`) just runs a built-in hello-world guest that issues one
`write` via `vmcall`.

```
        HOST (reverie-kvm)                 ┃   KVM guest (bare, real mode)
 ┌───────────────────────────────────┐    ┃  ┌──────────────────────────┐
 │ KvmBackend { vcpu, vm, memory }   │    ┃  │ vmcall/vmmcall; hlt       │
 │  run(|SyscallRequest, mem| i64)   │◄───╂──┤ frame @ GPA: {nr, args×6} │
 │  (host executes the syscall)      │    ┃  │                          │
 └───────────────────────────────────┘    ┃  └──────────────────────────┘
   No Tool, no GlobalState, no Guest — a syscall transport spike only.
```

- Address-space model: **not applicable yet** — there is no tool half to place.
  The design (i) would be multi-space; the crate (ii) is a transport spike.

---

## 4. How Detcore is instantiated today (hermit)

- **Detcore is a `reverie::Tool`/`GlobalTool` pair, backend-agnostic.**
  - `#[reverie::tool] impl<T: RecordOrReplay> Tool for Detcore<T>`
    (`hermit/detcore/src/lib.rs:446`): `GlobalState = detcore::GlobalState`,
    `ThreadState = ThreadState<T::ThreadState>`. Default `T = NoopTool`.
  - `#[reverie::global_tool] impl GlobalTool for GlobalState`
    (`hermit/detcore/src/tool_global.rs:325`): `Config = detcore::Config`,
    `Request = (DetTime, GlobalRequest)`, `Response = (Option<LogicalTime>,
    GlobalResponse)`.
  - `detcore/Cargo.toml`: `[dependencies]` = **`reverie` only**; `reverie-ptrace`
    is a **dev-dependency**. Detcore has no dependency on `reverie-dbi` /
    `reverie-kvm`. (The concrete backends are dependencies of `hermit-cli`.)
- **Only ptrace launches it.** `hermit-cli/src/lib.rs:321-325`:
  `reverie_ptrace::TracerBuilder::<Detcore>::new(command).config(cfg)
  .spawn().await?.wait().await?` → `(ExitStatus, GlobalState)`. Record/replay
  use `Detcore<Recorder>` / `Detcore<Replayer>` the same way
  (`record.rs`, `replay.rs`).
- **Backend selection.** `enum Backend { Ptrace, Dbi, Kvm }`
  (`hermit-cli/src/lib.rs:204`). `unavailable_reason` (`lib.rs:252-271`) reports
  Dbi as *"the DynamoRIO prototype does not yet expose a Detcore process
  launcher"* and Kvm as *"the bare KVM prototype cannot execute Linux programs
  without a guest-kernel ABI"*; `ensure_backend_dispatch` (`lib.rs:274-285`)
  hard-errors for any non-ptrace backend. Dispatch: `run.rs:754-758` sends Dbi
  → `run_dbi` and Kvm → `run_kvm`, both demo prototypes.
- **`GlobalState`: interface-ready, implementation single-space.** Documented as
  *"a singleton … [that] lives inside a central address space, generally the
  'tracer'"* (`tool_global.rs:155-158`). Its RPC surface is serializable
  (cross-space-capable), but the impl uses `Arc<Mutex<Scheduler>>`,
  `Arc<Mutex<GlobalTime>>`, atomics, and a local `tokio::task` scheduler loop,
  and carries TODOs to *"eventually … [put] the vector clock … in shared
  memory"* (`tool_global.rs:181-189, 386-389`). So moving Detcore to a
  multi-address-space backend is gated on making `GlobalState`/`ThreadState`
  transport their state, not on changing the trait signatures.

---

## 5. Address-space model — summary table

| Backend | Tool code runs in | Global state runs in | # tool address spaces | RPC transport | Tool API | Hosts Detcore? |
| --- | --- | --- | --- | --- | --- | --- |
| ptrace (`reverie-ptrace`) | tracer process | tracer process | **1** (centralized) | in-proc call + bincode round-trip | shared async `reverie::Tool` | **Yes** (only one) |
| SaBRe (`reverie-sabre`) | **inside guest** (`.so`) | host process | **2** | Unix socket, fd 100 (`reverie-rpc`) | *separate* sync `reverie_sabre::Tool` | No (separate API) |
| DynamoRIO (`reverie-dbi`) | inside guest (drrun) | in-process `()` today | **1** today (should be 2) | in-proc `receive_rpc` shortcut | shared async `reverie::Tool` | No (hosts `PrototypeTool`) |
| KVM design (proposal) | Rust process | Rust process | **2+** | memfd rings + eventfds, Sentry bridge | shared async `reverie::Tool` | Yes (as designed) |
| KVM crate (`reverie-kvm`) | — (none yet) | — | n/a | `vmcall` syscall transport | none | No |

---

## 6. Gap analysis vs. the multi-address-space vision

The vision (from the task): a launcher/global-state binary + a `.o`/`.so`
carrying the Tool; DBI backends load the tool `.so` into the guest and RPC to
global state; KVM keeps detcore as the "kernel" in its own space; ptrace may
dlopen the tool `.so` for consistency (user agnostic).

1. **Core traits: already aligned.** No signature changes are required to place
   tool and global state in separate address spaces (§1). Small additive core
   changes the backends want (per `kvm_backend_design.md`): `Auxv::from_entries`,
   explicit backend capability discovery, and (later) a backend-neutral register
   type instead of `libc::user_regs_struct`.

2. **The "split the tool into a `.so`" mechanism already exists** — for the
   *separate synchronous* API (`reverie-sabre` cdylib + `sbr_init` +
   `reverie-rpc`/`reverie-host`). The **gap** is that this is not the shared
   async `reverie::Tool`, so Detcore cannot be dropped into it. Unifying would
   mean either (a) porting the async `reverie::Tool` dispatch into an in-guest
   loader (an async executor + suspension inside the injected `.so`), or
   (b) accepting two tool APIs and writing a Detcore-flavored sabre tool.

3. **DynamoRIO (`reverie-dbi`) is the closest to the vision on the shared API**
   but took the single-address-space shortcut. To realize it: replace the
   one-shot `run_ready` with a real executor, bind a generic `DbiTracer<T>`
   reachable from the C ABI (instead of `static PrototypeTool`), and implement a
   **cross-process `GlobalRPC`** (socket/shared-mem + serialization) so one
   central Detcore `GlobalState` serves all instrumented guests. This is
   spec'd in the DBI gap doc (P1a–P3).

4. **KVM** is furthest out. The recommended design *is* multi-address-space
   (tool/global in a Rust process, Linux behavior in gVisor). The current
   `reverie-kvm` crate is only a `vmcall` transport spike; it has no tool half,
   so no address-space model applies yet.

5. **Detcore's `GlobalState` is the cross-cutting blocker for any multi-space
   backend.** Its interface is serializable, but its state lives in
   `Arc/Mutex`/atomics + a local scheduler task. Any backend that puts the tool
   in a *different* process than `GlobalState` must first make Detcore's global
   (and thread) state actually transportable / shared-memory-backed. This work
   is in `detcore`, is backend-independent, and is currently only sketched by
   TODOs.

### Suggested reading order for follow-up

`reverie/reverie/src/tool.rs` + `guest.rs` (contract) →
`ai_docs/transient/20260722_dbi-reverie-interface-gap.md` (DBI gaps, roadmap) →
`reverie/experimental/{reverie-rpc,reverie-host,reverie-sabre,riptrace}` (working
multi-space stack) → `ai_docs/kvm_backend_design.md` (KVM proposal) →
`hermit/detcore/src/tool_global.rs` (the GlobalState that must become
transportable).

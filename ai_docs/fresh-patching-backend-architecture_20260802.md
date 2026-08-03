# Fresh patching-backend architecture read: SaBRe, LiteInst, and e9patch

Date: 2026-08-02; refreshed 2026-08-03

## Decision

**The owner's hypothesis is confirmed for the production Hermit e9patch path.**
Hermit rewrites the main ELF with e9tool's `before empty` patch and then selects
the ordinary ptrace runtime; the sole `Detcore` instance and its `Guest` live in
the ptrace host. `DETLOG` is consequently emitted by the host, not by the
process executing the rewritten binary. Reverie's separate generic e9patch
backend has a more meaningful AOT event source, but even there every rewritten
syscall raises a validated `SIGTRAP` and dispatches the `Tool` in
`reverie-ptrace`.

This is the wrong steady-state architecture for a patching backend whose value
is an in-process fast path. It is not an intrinsic limitation of e9patch: the
tree already contains a separate direct AOT lane that constructs `T: Tool` in a
preload DSO and calls it in ordinary guest context. That lane is not Hermit's
production backend. An active, unlanded LiteInst PR stack extends the same model
to plain fork, per-thread coordinator RPC, quiescent hot hooks, and a
lifecycle-only ptrace supervisor. It still documents thread clone/clone3/vfork,
exec rebootstrap, unpatchable-site fallback, PMU preemption, and end-to-end vDSO
routing as unsupported. This is strong design evidence, not current-main
behavior.

The convergence target should be:

> **SaBRe-like placement, implemented through a shared e9patch/LiteInst
> guest-tool runtime.** Keep `Tool`, `ThreadState`, `Guest`, syscall policy, and
> `DETLOG` in the guest; keep only `GlobalTool`, deterministic coordination, and
> thin lifecycle/coverage supervision in the host. E9patch AOT rewriting,
> LiteInst live patching, and SaBRe loader rewriting should be adapters beneath
> that common runtime.

Saying only "LiteInst-like" is unsafe because LiteInst currently has two
architectures. Hermit's production LiteInst backend is ptrace-hosted just like
e9patch. LiteInst's opt-in direct lane is guest-hosted and is the useful source
of common implementation code. Semantically, therefore, choose the **SaBRe
model**; mechanically, extract the **LiteInst/e9patch direct-lane common code**.

## Evidence boundary

This is a fresh source trace, not an inference from backend names or benchmark
results. I read:

- Hermit main `e072d313ba62fdbd46c6708b40e5b407006946af`.
- Hermit's actual Reverie dependency pin
  `d973a85b328610c14c41c39fa57495b9f77c3c90`.
- Current Reverie main `d2fb9a055693bec30e8d48333c5694050b22e869`.
- Current LiteInst2 main `8bffae9da68e0636ec4b6dc473a0fd29ac589d20`.

The Hermit pin is an ancestor of current Reverie main. Both revisions retain
the production placement described below. The active in-guest LiteInst stack is
reviewed separately as proposed architecture, not silently treated as landed.
The earlier architecture map in [Reverie PR #324][pr-324] was used only after
retracing current launch, callback, trap, `Tool`, `Guest`, and DETLOG paths.

## Current production paths at a glance

| Backend/path | Patch or event source | `Tool` / `ThreadState` | `Guest` | `GlobalTool` | Per-syscall `DETLOG` process |
| --- | --- | --- | --- | --- | --- |
| **Hermit SaBRe** | SaBRe loader rewrites sites; Hermit's ptrace supervisor converts missed raw `syscall` sites into SaBRe markers | In each injected guest process | `SabreGuest`, in guest | Hermit coordinator via UDS RPC | Guest plugin; raw forwarded records are extracted during verification |
| **Hermit LiteInst** | First seccomp stop installs a live hook; hot hook emits a validated `SIGTRAP` | Sole instance in ptrace host | Reverie ptrace task | Same host process | Ptrace host |
| **Hermit e9patch** | CLI applies e9tool `before empty` to main ELF, then coerces runtime backend to ordinary ptrace | Sole instance in ptrace host | Reverie ptrace task | Same host process | Ptrace host |
| **Reverie e9patch generic `Backend`** | AOT callback builds an injected frame and emits validated `SIGTRAP` | Sole instance in ptrace host | Reverie ptrace task | Same host process | Ptrace host |
| **LiteInst direct opt-in** | SIGSYS installs hook; hook calls guest `ToolHost` in ordinary context | In preload DSO | `LiteinstGuest`, in guest | External `RpcServer` | Guest, if a sink is installed |
| **e9patch direct opt-in** | AOT callback calls shared preload dispatcher directly | In preload DSO | `E9patchGuest`, in guest | External `RpcServer` | Guest, if a sink is installed |

The critical distinction is not whether ptrace is attached. SaBRe has a ptrace
supervisor, but that supervisor does not own `Detcore` or execute
`Tool::handle_syscall_event`. Current e9patch and LiteInst do.

## Actual architecture by backend

### SaBRe: guest-local policy with host coordination and supervision

Hermit creates one coordinator-owned `detcore::GlobalState` and one
`RpcServer`, then launches the SaBRe loader with `libdetcore_sabre.so`
([coordinator launch][hermit-sabre-launch]). The plugin explicitly describes
itself as executing Detcore "inside each guest process"
([plugin placement][detcore-sabre-intro]), constructs a
`RemoteReverieAdapter<Detcore>`, and forwards loader callbacks to that adapter
([Detcore plugin][detcore-sabre-plugin]).

The literal syscall path is:

1. SaBRe's rewritten trampoline enters the injected callback
   `handle_syscall<T>` ([loader callback][sabre-callback]).
2. The callback invokes the plugin's `Tool::syscall`.
3. The plugin calls `RemoteReverieAdapter::handle_syscall`.
4. The adapter constructs `SabreGuest` around the process-local thread state and
   calls `self.tool.handle_syscall_event(&mut guest, syscall)` in that process
   ([remote adapter][sabre-adapter]).
5. Only `GlobalRPC` requests cross the per-thread blocking UDS connection to the
   coordinator ([SaBRe Guest/RPC][sabre-guest]).

Hermit's `sabre_ptrace` worker is a lifecycle and missed-site safety net, not the
tool host. On a raw syscall-entry stop it replaces `0f 05` with SaBRe's reserved
marker, cancels the in-flight kernel syscall, rewinds RIP, and resumes so the
SaBRe handler owns the event. It also tracks clone/fork/vfork/exec/exit
([SaBRe supervisor][sabre-supervisor]). This is the architectural pattern the
other patching backends should copy: host supervision without host policy
dispatch.

Backend-private SaBRe code is substantial: the external loader/rewriter and
plugin ABI; callback, recursion, thread, fork/vfork, signal, vDSO, protected-FD
and protected-file machinery; `RemoteReverieAdapter`; `SabreGuest`; Hermit's
Detcore-specific plugin; and Hermit's missed-site supervisor. It shares Reverie
traits and RPC transport, but not `reverie-preload` or a ptrace `Guest`.

### LiteInst: two real lanes, with Hermit selecting the host lane

#### Production Hermit lane

Hermit explicitly calls
`LiteinstBackend::run_host_with_preload::<Detcore>`
([Hermit dispatch][hermit-dispatch]). That API states that ptrace owns the sole
`Tool` and `GlobalTool`; the preload only installs sites and produces hot-site
traps ([LiteInst host API][lite-host-api]). Hermit's own startup diagnostic says
"Detcore Tool active in ptrace host."

On first use, ptrace handles a seccomp stop, invokes the in-guest LiteInst
installation helper, records the hook footprint, and services the logical
syscall in the host. A hot hook later enters the runtime's marker trap. The
ptrace task validates the marker, stack, return site, frame, runtime generation,
and active hook; then it calls `handle_injected_syscall`
([LiteInst trap validation][lite-ptrace-trap]). That function calls the
ptrace-owned process state's `handle_syscall_event(self, syscall)`
([ptrace injected dispatch][ptrace-injected-dispatch]).

Thus live patching changes how the host receives an event; it does not move
policy into the guest. Every successfully installed hot hook still incurs a
ptrace `SIGTRAP` stop.

#### Guest-local direct lane

LiteInst separately exposes `run_with_preload`/`launch`, which creates an
external `RpcServer` and injects a tool-specific DSO
([LiteInst direct launch][lite-direct-launch]). Its constructor connects a
`CoordinatorRpc`, constructs local `T`, and registers `ToolHost<T>`
([LiteInst ToolHost][lite-tool-host]). A first-use SIGSYS handler installs a
replace-first LiteInst hook and defers execution to the trampoline; the
trampoline later calls the tool host in ordinary context
([LiteInst dispatcher][lite-dispatcher]).

That lane has the desired placement, but current main still rejects unsupported
clone/fork/exec forms at its generic-tool boundary. Generic Rust `Tool` code
cannot run in SIGSYS context, so an unpatchable subscribed residual fails closed
rather than falling back to the same tool. Hermit does not select this lane.

#### Active candidate stack, not current main

The open [Reverie in-guest stack][lite-guest-pr] adds plain-fork state
reconstruction and shared coordinator RPC, then layers per-thread RPC,
quiescent patching, and a [lifecycle-only ptrace supervisor][lite-supervisor-pr].
The supervisor deliberately runs `TracerBuilder<()>` with no syscall
subscriptions: ptrace owns task discovery, vDSO patching, exact root status,
descendant draining, and signal-death observation, while the guest-local `Tool`
remains the sole syscall handler. The matching [Hermit integration
stack][hermit-lite-guest-pr] stages a guest-resident `Detcore`.

This is the strongest concrete model for e9patch convergence, but it is not yet
landed. Its own PR boundary still lists thread clone/clone3/vfork, exec
rebootstrap, an unpatchable-site slow path, PMU preemption, and end-to-end vDSO
time routing as unsupported. It should be evaluated as an implementation
candidate, not used to describe production LiteInst or make performance claims
about current main.

Backend-private LiteInst code includes LiteInst2 scanning, trampoline and live
publication; guest patch allocation and straddler handling; the host handshake;
runtime marker assembly; first-site installation and mapping invalidation;
LiteInst-specific ptrace task state; statistics; and `LiteinstGuest`.

### e9patch: AOT patching, but production policy remains in ptrace

#### Production Hermit lane

Hermit does not run Reverie's generic `E9patchBackend`. Its internal preparer
asks e9tool to apply `before empty` at the instruction-map sites
([Hermit e9 rewrite][hermit-e9-rewrite]). The CLI overlays that rewritten main
executable, then `runtime_backend()` converts the selected e9patch backend to `Backend::Ptrace`
([Hermit e9 selection][hermit-e9-selection], [Hermit e9 preparation][hermit-e9-prep]).
The common run function then constructs `TracerBuilder::<Detcore>` in Hermit
([Hermit dispatch][hermit-dispatch]). This alone confirms the owner's placement
hypothesis for the shipped path: Hermit's patch is not its syscall event
transport at all; ordinary ptrace/seccomp remains that transport.

#### Reverie generic backend

The generic Reverie backend has the same architecture. `e9tool` replaces every
recovered root-ELF `syscall` with an embedded payload and rejects partial
recovery or signal-based B0 sites ([e9 rewrite][e9-rewrite]). Its production
contract says ptrace remains attached for lifecycle, shared-library sites,
signals, timers, and arbitrary `Guest` operations
([e9 backend contract][e9-backend-contract]).

The AOT payload creates an injected syscall frame and traps. Ptrace validates
the trap's marker, RIP, rewritten-site provenance, and frame, then calls the
same host `handle_injected_syscall` used above. `Backend::run` waits on that
tracer ([e9 generic run][e9-generic-run]). The AOT patch therefore avoids the
ordinary seccomp entry path for recovered sites, but not the ptrace context
switch or host `Tool` dispatch.

#### Guest-local direct lane

The codebase also proves a different architecture is feasible. `run_direct`
rewrites the ELF, starts an external `RpcServer`, preloads a tool-specific DSO,
and reports `event_source=aot-callback; controller=in-process-seccomp`
([e9 direct launch][e9-direct-launch]). The AOT bridge calls
`reverie_preload::trap::dispatch_direct`, and `ToolHost<T>` constructs a local
`E9patchGuest` and invokes `tool.handle_syscall_event`
([e9 AOT callback][e9-aot-callback], [e9 ToolHost][e9-tool-host]).

This is a real guest-local lane, but the public docs deliberately keep it
separate from `Backend::run` until lifecycle is complete. Residual subscribed
SIGSYS events cannot safely invoke arbitrary Rust in signal context and fail
closed after activation. Static binaries, early loader calls, exec, and general
process/thread expansion are not production-complete.

Backend-private e9patch code includes external-tool snapshotting and invocation,
coverage/provenance validation, sealed rewritten artifacts, the embedded payload
and AOT frame ABI, overlay/cache integration in Hermit, startup-runtime
exceptions, direct-callback publication, residual classification, and
`E9patchGuest`.

## DETLOG placement and transport

`detlog!` emits through tracing and, when installed, a process-local forwarder
([DETLOG macro][detlog-macro]). Detcore emits inbound and finish records inside
`Tool::handle_syscall_event`
([inbound DETLOG][detlog-inbound], [finish DETLOG][detlog-finish]). Therefore the
physical process running the `Detcore` object is the emitting process:

- **SaBRe:** guest plugin.
- **Hermit LiteInst:** ptrace host.
- **Hermit e9patch:** ptrace host.
- **e9patch/LiteInst direct lanes:** guest preload DSO.

SaBRe's guest plugin now installs a process-local allocation-free/TLS-free raw
sink before constructing the adapter, and Hermit extracts those records from
diagnostic stderr during verification ([landed SaBRe forwarder][sabre-forwarder],
[Hermit PR #1448][sabre-detlog-pr]). Follow-up checks require syscall records
and remove the forwarded lines from guest-visible stderr. This closes the prior
visibility gap without moving the `Tool` out of the guest.

The coordinated `audit_cross_backend_detlog` read still found no true merged
cross-process DETLOG stream. The implementation appends normalized captured
guest records after the host log, so it establishes parity coverage but not
semantic interleaving between host and guest events.

For convergence, copying diagnostic stderr is an acceptable short-term proof,
not the final shared protocol. A common guest runtime should emit a framed
record containing at least stable process/thread identity, a per-thread
sequence, event phase, and payload. The coordinator should publish records in
deterministic scheduler-commit order. Raw pipe/socket arrival order is adequate
only while Detcore serializes all runnable guest callbacks; it does not define
future inter-process parallel ordering. The sink must remain allocation-free,
TLS-destruction-safe, recursion-safe, and protected from guest close/dup/splice
operations. PR #1448 is the right low-level sink prototype, while the ordering
and framing belong in shared Reverie/Detcore transport.

## What is shared today

| Layer | SaBRe | LiteInst | e9patch | Actual sharing |
| --- | --- | --- | --- | --- |
| `Tool`, `GlobalTool`, `Guest`, subscriptions | Yes | Yes | Yes | Shared `reverie-core` contracts |
| Coordinator server/wire | `RpcServer` + `BlockingRpcClient` | `RpcServer` + preload `CoordinatorClient` | Same as LiteInst | Shared `reverie-rpc-transport` wire; client adapters differ |
| Ptrace engine | Private thin Hermit supervisor; no ptrace `Guest` | Production `TracerBuilder<T>` plus LiteInst extensions | Production `TracerBuilder<T>` plus validated injected trap | LiteInst/e9patch share host `Tool` placement through `reverie-ptrace` |
| In-process trap runtime | SaBRe-private callback/SIGILL machinery | `reverie-preload` SIGSYS/trusted gate | Same `reverie-preload` runtime for direct/residual lane | Shared only between LiteInst and e9patch |
| Guest-local generic tool host | `RemoteReverieAdapter` + `SabreGuest` | Private `ToolHost` + `LiteinstGuest` | Private `ToolHost` + `E9patchGuest` | Semantics overlap, source is not shared |
| Host launch/coordinator lifecycle | Hermit-private | Private `launch<T>` | Private `launch_direct<T>` | Duplicated between LiteInst/e9patch |
| Patch implementation | External SaBRe loader | LiteInst2 live hooks | External e9tool/AOT payload | Intentionally private |
| Signal/callback/thread lifecycle | SaBRe-private | Shared preload primitives plus LiteInst-private runtime | Shared preload primitives plus e9-private exceptions | Partial sharing only |
| DETLOG forwarding | Landed backend-private raw stderr sink plus verifier extraction | Host subscriber in production | Host subscriber in production | No structured guest-runtime transport shared by all three |

`reverie-preload` is already the correct mechanism/policy boundary for e9patch
and LiteInst: it owns the seccomp filter, SIGSYS handler, trusted syscall gate,
event-source tagging, and `SyscallDispatcher`
([preload dispatch seam][preload-dispatch]). It also documents a
`LifecycleController` seam, but `HybridPtrace` is only an unsupported skeleton
today ([preload lifecycle seam][preload-lifecycle]).

The remaining duplication is concrete, not speculative:

- `reverie-liteinst/src/rpc.rs` is 124 lines and
  `reverie-e9patch/src/rpc.rs` is 128 lines; a no-index diff is only 9 additions
  and 5 deletions. Both wrap the same preload `CoordinatorClient`.
- Both private tool hosts duplicate installation/bootstrap, local `T`
  construction, subscription caching, per-thread state maps, post-exec/start/
  exit delivery, synchronous future driving, tail injection, `GlobalRPC`,
  protected coordinator-FD logic, `Guest` plumbing, and fatal paths.
- Both launchers duplicate temp UDS creation, `GlobalState`/`RpcServer` startup,
  sealed bootstrap handling, `LD_PRELOAD` composition, output draining, connection
  readiness, child wait, and `Arc<GlobalState>` recovery.

SaBRe cannot reuse those files verbatim because its loader callback, memory,
stack, register, clone, and signal contracts differ. It can share the higher
level process-local tool session, coordinator contract, DETLOG sink, and
lifecycle event model after those are separated from preload-specific frames.

## Recommendation: one guest-local tool runtime, three patch adapters

The answer to "SaBRe-like or LiteInst-like?" is deliberately two-level:

- **Placement and correctness model: SaBRe-like.** One process-local `Tool` and
  `ThreadState` own every subscribed syscall; ptrace may supervise lifecycle and
  coverage but never becomes a second policy owner.
- **Implementation template: the in-guest LiteInst candidate.** Its direct
  `ToolHost`, shared coordinator RPC, and no-subscription lifecycle supervisor
  are already structurally close to e9patch's direct lane. E9patch should become
  near-identical to that runtime, differing primarily in AOT patch creation,
  provenance, residual discovery, and frame adaptation.

Current production LiteInst is not the template: it has the same host-dispatch
defect as e9patch. The target is specifically the guest-local LiteInst lane and
its active lifecycle-supervisor work.

### 1. Extract behavior before changing placement

Create a shared sibling of `reverie-preload` (working name
`reverie-inprocess-tool`) or a clearly separated module within it. Do not put
e9patch or LiteInst names in the shared API.

Extract these exact responsibilities first, with no dispatch change:

- `CoordinatorRpc<G>` and protected-FD handling; delete the near-identical e9
  and Lite wrappers.
- `ToolCoordinator<G>` for UDS creation, `RpcServer`, bootstrap, child wait,
  connection readiness, output draining, and global-state recovery.
- `ToolSession<T>` for process-local `T`, subscription cache, thread-state
  creation/inheritance/removal, start/post-exec/exit callbacks, synchronous
  future driving, tail injection outcome, and process-local DETLOG installation.
- Shared nested-tool-syscall and reserved-FD policy.

Keep `Guest` implementations separate initially. They encode real backend
differences and are a poor place to force premature genericity.

### 2. Define a narrow ordinary-context event adapter

Each patcher should deliver an event to `ToolSession` through a narrow adapter:

```text
GuestEventFrame
  number / args / instruction_pointer / source
  set_result / defer_to
  register access needed by Guest

PatchEventSource
  initialize process runtime
  classify direct vs residual event
  publish/retire patch
  report mapping invalidation and provenance
```

The common layer owns policy dispatch. The adapter owns only how a syscall frame
was produced and how results return to the guest.

- e9patch adapter: AOT `InjectedSyscallFrame` and direct callback page.
- LiteInst adapter: `HookContext`, first-use installation, and dynamic
  trampoline.
- SaBRe adapter: loader callback stack frame and injected-call ABI.

Patch scanning, rewriting, trampoline generation, publication, and provenance
remain private. This is the requested "near-identical except patching" boundary.

### 3. Make ptrace a lifecycle/coverage owner, never a second Tool host

Implement the existing `HybridPtrace` concept as a real host-side supervisor,
using the SaBRe supervisor as the proven semantic model:

- Own startup, exec, fork/clone/vfork, signal, vDSO, and mapping notifications.
- Establish the guest runtime and coordinator connection before accepting tool
  events.
- On a missed subscribed site, route execution to a guest-resident
  ordinary-context fallback entry or install a patch; do not call a host
  `Detcore`.
- Fail closed if a subscribed event cannot reach the one guest-local tool
  session.

This last rule matters. A "fast sites in guest, residuals in host" design would
create two `Detcore`/`ThreadState` owners and split DETLOG ordering. It is not a
valid convergence even if common cases are fast.

E9patch has the hardest residual problem because its current AOT pass rewrites
only the root ELF. Shared-library/JIT/late-mapped sites need either load-time
rewriting, a guest fallback thunk installed by the supervisor, or the common
dynamic fallback patcher. Keeping host policy dispatch for those sites is not an
acceptable endpoint.

### 4. Standardize DETLOG at the same boundary

Install the shared no-allocation sink whenever `ToolSession<Detcore>` is created.
Transport structured records over a protected diagnostic channel. The host
merges them by deterministic scheduling identity/commit order and feeds the
existing verifier. SaBRe, e9patch direct, and LiteInst direct should use exactly
the same framing, filtering, and zero-record fail-closed check.

### 5. Migrate in controlled stages

1. **Stabilize the LiteInst candidate boundary:** land the guest-local Tool path
   only with its lifecycle-only supervisor and explicit fail-closed unsupported
   cases. Preserve a measured host-lane reference during migration.
2. **Common-code extraction:** RPC wrapper, coordinator launcher, `ToolSession`,
   and DETLOG transport. Prove no behavior change in LiteInst and both existing
   direct lanes.
3. **E9patch guest-local experimental lane in Hermit:** wire the existing direct
   AOT callback to the exact shared session used by LiteInst, initially behind
   an explicit non-default flag.
4. **Lifecycle and residual completeness:** reuse the no-subscription supervisor
   and ensure every subscribed event reaches the same guest tool session across
   DSOs, fork/clone/vfork, exec, vDSO, signals, and mapping changes.
5. **Production switch:** require ptrace output parity, strict verify, full
   corpus compatibility, zero host `handle_syscall_event` calls, nonzero
   guest-forwarded DETLOG, and same-host performance attribution before making
   guest-local e9patch the default.
6. **Retire LiteInst host dispatch:** after the guest lane covers the supported
   production envelope, retain LiteInst2 only as its patch adapter.
7. **SaBRe consolidation:** adapt `RemoteReverieAdapter` to the shared
   coordinator/session/DETLOG pieces while retaining SaBRe callback, loader,
   memory, signal, and patch code.

## Tradeoffs and non-goals

### Why not copy current Hermit LiteInst?

It is mature enough to run Hermit because ptrace provides complete `Guest`
semantics, signal/timer control, process lifecycle, and an existing host tracing
subscriber. But it also preserves the exact architectural defect under review:
each installed hook returns through ptrace and `Detcore` remains in the host.
Converging e9patch to this model would share more code while discarding the main
reason to use an in-process patcher.

### Why not literally transplant SaBRe internals?

SaBRe has the right placement and more developed per-thread/fork state, but its
loader ABI, callbacks, memory/stack adapter, recursion rules, SIGILL handling,
and signal machinery are deeply backend-specific. Literal reuse would couple
e9patch to SaBRe rather than isolate the patcher. The reusable design is its
placement and supervision split, not its loader implementation.

### Costs of the recommended model

- Guest-local Rust tool execution must control reentrancy, allocation, locks,
  TLS destruction, and tool-issued syscalls. Direct callbacks must stay in
  ordinary context; signal handlers may discover/install/defer only.
- Blocking coordinator RPC can suspend a guest callback. Current SaBRe's
  `poll_once`/tail-inject contract and each backend's syscall injection semantics
  must be made explicit in the shared session.
- A full `Guest` is not just a syscall frame. Memory, registers, stack, signal
  state, injection, tail injection, timers, and process lifecycle need parity
  tests before switching production.
- One preload DSO/tool instance per process increases guest runtime state and
  makes fork/exec reconnection a correctness boundary.
- Deterministic cross-process DETLOG merging becomes a protocol responsibility
  instead of an incidental property of one host subscriber.

These costs are real, but they are already present in SaBRe and the direct
e9patch/LiteInst prototypes. Keeping production policy in ptrace avoids the
costs by avoiding the intended architecture.

The prior same-SHA scorecard in [PR #324][pr-324] found current e9patch faster
than SaBRe across its clean intersection. That is useful performance evidence,
but it neither proves host placement correct nor predicts the gain from moving
policy in-process. Preserve e9patch's AOT rewriter and measure each migration
stage; do not replace it with the SaBRe loader merely to obtain SaBRe-like
placement.

## Performance hypothesis and attribution protocol

**Hypothesis, not a result:** a complete guest-local patching backend could be
the performance leader for syscall-heavy steady-state workloads. Like gVisor's
systrap/usertrap fast path, it can avoid a ptrace stop and host context switch,
and unlike KVM it requires no hardware virtualization. SaBRe already has the
right Tool placement; LiteInst can patch dynamically; e9patch can amortize an
AOT rewrite. None is yet evidence that the full deterministic system wins:
guest callbacks still execute Detcore policy, may block on coordinator UDS RPC,
and must pay for residual handling, signals, mapping changes, and lifecycle
coverage. The gVisor analogy also does not imply equivalent security or syscall
coverage; systrap combines its patched fast path with a fail-closed interception
floor.

Future leader claims must report these costs separately:

1. **Instrumentation cost:** run native, gVisor, and every backend under the
   same one-CPU affinity/cpuset, with the same workload, warmup, repetitions,
   and absolute wall-time or ns/op anchors. A minimal counter Tool isolates
   event-transport cost; it does not represent Hermit determinization.
2. **Determinization cost:** on that same single-CPU allocation, report Hermit
   relaxed and strict modes separately. Attribute the delta from the matching
   counter/direct lane to Detcore policy, scheduling, logging, and coordinator
   RPC rather than to patching alone.
3. **Sequentialization cost:** run a separate 1-to-N CPU scaling experiment.
   Hermit serializes guest-thread execution, so a parallel program can lose its
   N-core speedup before instrumentation overhead is counted. Report native and
   backend absolute times at each CPU count and the lost scaling factor; never
   fold this opportunity cost into an "instrumentation slowdown."
4. **Startup versus steady state:** split cold e9tool preprocessing, first-hit
   SIGSYS/patch installation, and already-patched syscall time. Report patch
   coverage and residual/fallback counts beside timings.
5. **Semantic envelope:** require comparable syscall/vDSO/signal/lifecycle
   coverage, strict-verify evidence where claimed, zero host
   `handle_syscall_event` calls for the guest-local configuration, and a
   fail-closed account of every unpatchable subscribed site.

This matrix may show that a patcher beats ptrace, DBI, or KVM on a particular
steady-state workload. Until the single-core instrumentation result, the
separate N-core sequentialization result, and the coverage counters all exist,
that remains a workload-specific hypothesis rather than an architectural fact.

## Validation notes

- Traced every production selector to the concrete `Tool::handle_syscall_event`
  call rather than relying on docs or backend enum names.
- Compared Hermit's pinned Reverie code with current Reverie main; the production
  placement paths are unchanged.
- Reviewed the active in-guest LiteInst PR stack and kept it explicitly separate
  from current-main architecture.
- Rechecked the coordinated `audit_cross_backend_detlog` finding after the SaBRe
  forwarder landed: visibility is fixed, but host/guest records are not a
  semantically interleaved stream.
- An exact Hermit `c7531a83` binary smoke was not suitable as independent
  multi-backend proof: its SaBRe feature was absent, LiteInst failed its runtime
  activation handshake in this environment, and `/bin/true` reported zero
  e9patch candidate sites before succeeding through ptrace. Those observations
  are recorded as limitations, not used to infer architecture.
- Research only: no product code was changed.

[pr-324]: https://github.com/rrnewton/reverie/pull/324
[sabre-detlog-pr]: https://github.com/rrnewton/hermit/pull/1448
[lite-guest-pr]: https://github.com/rrnewton/reverie/pull/326
[lite-supervisor-pr]: https://github.com/rrnewton/reverie/pull/337
[hermit-lite-guest-pr]: https://github.com/rrnewton/hermit/pull/1451
[sabre-forwarder]: https://github.com/rrnewton/hermit/blob/e072d313ba62fdbd46c6708b40e5b407006946af/detcore-sabre/src/lib.rs#L34-L116
[hermit-sabre-launch]: https://github.com/rrnewton/hermit/blob/e072d313ba62fdbd46c6708b40e5b407006946af/hermit-cli/src/lib.rs#L988-L1096
[detcore-sabre-intro]: https://github.com/rrnewton/hermit/blob/e072d313ba62fdbd46c6708b40e5b407006946af/detcore-sabre/src/lib.rs#L9-L27
[detcore-sabre-plugin]: https://github.com/rrnewton/hermit/blob/e072d313ba62fdbd46c6708b40e5b407006946af/detcore-sabre/src/lib.rs#L139-L250
[sabre-callback]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/experimental/reverie-sabre/src/callbacks.rs#L497-L567
[sabre-adapter]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/experimental/reverie-sabre/src/reverie_adapter.rs#L422-L568
[sabre-guest]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/experimental/reverie-sabre/src/reverie_adapter.rs#L1071-L1119
[sabre-supervisor]: https://github.com/rrnewton/hermit/blob/e072d313ba62fdbd46c6708b40e5b407006946af/hermit-cli/src/sabre_ptrace.rs#L351-L424
[hermit-dispatch]: https://github.com/rrnewton/hermit/blob/e072d313ba62fdbd46c6708b40e5b407006946af/hermit-cli/src/lib.rs#L1509-L1545
[lite-host-api]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-liteinst/src/backend.rs#L192-L274
[lite-ptrace-trap]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-ptrace/src/task.rs#L2209-L2388
[ptrace-injected-dispatch]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-ptrace/src/task.rs#L2010-L2088
[lite-direct-launch]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-liteinst/src/backend.rs#L352-L446
[lite-tool-host]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-liteinst/src/tool_host.rs#L72-L260
[lite-dispatcher]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-liteinst/src/runtime.rs#L1620-L1701
[hermit-e9-selection]: https://github.com/rrnewton/hermit/blob/e072d313ba62fdbd46c6708b40e5b407006946af/hermit-cli/src/bin/hermit/run.rs#L1710-L1719
[hermit-e9-prep]: https://github.com/rrnewton/hermit/blob/e072d313ba62fdbd46c6708b40e5b407006946af/hermit-cli/src/bin/hermit/run.rs#L2513-L2555
[hermit-e9-rewrite]: https://github.com/rrnewton/hermit/blob/e072d313ba62fdbd46c6708b40e5b407006946af/hermit-cli/src/e9patch.rs#L264-L312
[e9-rewrite]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-e9patch/src/rewrite.rs#L197-L297
[e9-backend-contract]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-e9patch/src/backend.rs#L372-L458
[e9-generic-run]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-e9patch/src/backend.rs#L988-L1005
[e9-direct-launch]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-e9patch/src/backend.rs#L802-L935
[e9-aot-callback]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-e9patch/src/aot.rs#L162-L195
[e9-tool-host]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-e9patch/src/tool_host.rs#L72-L373
[detlog-macro]: https://github.com/rrnewton/hermit/blob/e072d313ba62fdbd46c6708b40e5b407006946af/detcore/src/detlog.rs#L13-L53
[detlog-inbound]: https://github.com/rrnewton/hermit/blob/e072d313ba62fdbd46c6708b40e5b407006946af/detcore/src/lib.rs#L1444-L1459
[detlog-finish]: https://github.com/rrnewton/hermit/blob/e072d313ba62fdbd46c6708b40e5b407006946af/detcore/src/lib.rs#L2247-L2257
[preload-dispatch]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-preload/src/dispatch.rs#L9-L30
[preload-lifecycle]: https://github.com/rrnewton/reverie/blob/d2fb9a055693bec30e8d48333c5694050b22e869/reverie-preload/src/lifecycle.rs#L9-L108

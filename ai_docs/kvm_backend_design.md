# KVM Backend Design

Status: research proposal

gVisor revision studied:
[`ae10899a7359a2d16d8babf0c40a512dc13c5c68`](https://github.com/google/gvisor/commit/ae10899a7359a2d16d8babf0c40a512dc13c5c68)
(2026-07-20)

## Executive summary

KVM can replace ptrace as the mechanism that transfers application execution
across a protection boundary, but KVM alone cannot execute Linux syscalls for
the application. A KVM backend must also provide a Linux ABI implementation.
This is the most important conclusion from gVisor's implementation.

gVisor does not VM-exit to the host for every application `SYSCALL`. It runs
application code at guest ring 3 and a small entry path plus the Sentry at guest
ring 0. The syscall instruction enters the guest ring-0 entry code, saves the
application register state, and returns a syscall event to the Sentry. The
Sentry then implements the syscall in its user-space Linux kernel. VM exits are
primarily needed when the Sentry itself makes a host syscall or when the VMM
must process an interrupt.

The recommended first backend is therefore:

1. Use gVisor's Sentry and KVM platform as the Linux execution engine.
2. Add a narrow, versioned interception bridge at the Sentry syscall and task
   lifecycle boundaries.
3. Run existing Reverie `Tool` implementations in a Rust `reverie-kvm`
   frontend. Implement `Guest<T>` as a proxy over that bridge.

This preserves the `Tool`, `GlobalTool`, and `Guest` source-level programming
model, reuses gVisor's process, memory, signal, and syscall implementations, and
keeps untrusted application code behind KVM. It does not preserve exact host
kernel behavior: the guest observes gVisor's Linux ABI and virtual PIDs, just as
any other gVisor workload does.

A standalone Rust VMM that replays syscalls in the host is possible as a
research project, but is not a smaller version of this design. Correct handling
of `clone`, `fork`, `execve`, `mmap`, signals, futexes, credentials, and virtual
memory would amount to building a second Linux personality. It should not be
the initial implementation.

## Goals and non-goals

### Goals

- Existing tools continue to implement the traits in the `reverie` crate.
- Support interception, argument replacement, suppression, repeated
  `Guest::inject`, and `Guest::tail_inject`.
- Preserve process and thread state callbacks across clone, fork, exec, and
  exit.
- Keep tool and global state in Rust, outside the Sentry process.
- Avoid bridge work for syscalls excluded by `Tool::subscriptions`.
- Initially support Linux x86-64 and externally executed commands.
- Place application instructions behind KVM and gVisor's sandbox boundary.

### Non-goals for the first release

- Bit-for-bit equivalence with syscalls executed by the host kernel.
- Running an arbitrary Rust closure with `spawn_fn` inside the guest.
- GDB server support.
- Precise retired-conditional-branch timers.
- CPUID and RDTSC callbacks.
- aarch64 support.
- Building a new user-space kernel or importing gVisor's KVM package into Rust.

## How gVisor uses KVM

### Components

The relevant layers are:

```text
gVisor Task goroutine and syscall dispatcher
                |
        platform.Context.Switch
                |
      KVM vCPU + guest page tables
                |
  application ring 3 <-> Sentry ring 0
                |
             /dev/kvm
```

The KVM platform is not a conventional whole-machine VMM. The Sentry and the
application share a KVM VM and switch privilege levels within it. gVisor maps
Sentry memory into the guest kernel half and maps each application's memory
through a separate guest page-table root.

### Initialization and memory

`pkg/sentry/platform/kvm/kvm.go` opens the KVM device, creates a VM with
`KVM_CREATE_VM`, and builds a `machine`. `machine.go` creates a pool of vCPUs.
Each vCPU owns a KVM vCPU fd, `kvm_run` mapping, ring-0 CPU state, and saved
application registers.

`address_space.go` builds guest page tables for each application address space.
`MapFile` obtains host mappings for Sentry-managed pages, registers their guest
physical backing with KVM, and maps them as guest user pages. The upper guest
address-space half contains shared Sentry/ring-0 mappings. `physical_map.go`
maintains an injective host-virtual to guest-physical mapping; this is tightly
coupled to the Sentry process address space.

This coupling is why `pkg/sentry/platform/kvm` is not a reusable Rust VMM
library. Its assembly assumes gVisor's `ring0.CPU` layout, Go runtime state,
Sentry page tables, signal frames, and dual mappings of Sentry code.

### Application entry

The hot path starts in
[`platformContext.Switch`](https://github.com/google/gvisor/blob/ae10899a7359a2d16d8babf0c40a512dc13c5c68/pkg/sentry/platform/kvm/context.go#L51).
It:

1. Acquires a vCPU.
2. Selects the application's guest page tables.
3. supplies the application register and floating-point state.
4. Calls `bluepill` and then `vCPU.SwitchToUser`.

On x86-64, `ring0.CPU.SwitchToUser` installs the user CR3 and enters guest ring
3 with `SYSRET` or `IRET`. The guest ring-0 setup programs `MSR_LSTAR` to the
`sysenter` assembly entry point.

### Syscall interception

For an application syscall, the path is:

```text
application SYSCALL
  -> ring0 sysenter assembly
  -> save syscall number, arguments, RIP, RSP, flags, and FP state
  -> return vector ring0.Syscall from SwitchToUser
  -> platformContext.Switch returns nil error
  -> Task.runApp calls Task.doSyscall
  -> gVisor seccomp/ptrace checks and syscall table dispatch
  -> set application return register
  -> enter application again
```

The decisive code is:

- [`ring0/entry_amd64.s`](https://github.com/google/gvisor/blob/ae10899a7359a2d16d8babf0c40a512dc13c5c68/pkg/ring0/entry_amd64.s#L448)
  saves user registers in `sysenter` and produces the syscall vector.
- [`vCPU.SwitchToUser`](https://github.com/google/gvisor/blob/ae10899a7359a2d16d8babf0c40a512dc13c5c68/pkg/sentry/platform/kvm/machine_amd64.go#L356)
  treats `ring0.Syscall` as the normal fast-path return.
- [`Task.runApp`](https://github.com/google/gvisor/blob/ae10899a7359a2d16d8babf0c40a512dc13c5c68/pkg/sentry/kernel/task_run.go#L239)
  interprets a nil platform error as a syscall event.
- [`Task.doSyscall`](https://github.com/google/gvisor/blob/ae10899a7359a2d16d8babf0c40a512dc13c5c68/pkg/sentry/kernel/task_syscall.go#L209)
  reads the saved syscall ABI registers and enters the Sentry syscall table.

The application syscall does not directly reach the host kernel. KVM provides
the privilege transition; the Sentry provides Linux behavior.

### Sentry host syscalls and interrupts

The Sentry must still make host syscalls for its own runtime and host services.
When Sentry code makes a syscall while running at guest ring 0,
`vCPU.KernelSyscall` rewinds the instruction pointer and executes `HLT`. The
halt causes a KVM exit. `bluepillHandler` drives `KVM_RUN`, restores a host
signal frame on exit, and lets the syscall execute from host ring 3. On a later
KVM entry, execution resumes after the halt.

The same bluepill machinery uses a signal and injected virtualization-exception
vector to interrupt a running vCPU. This provides preemption without making
every application syscall a host VM exit.

### Lessons for Reverie

1. KVM is the execution boundary, not the Linux syscall implementation.
2. The fast path depends on running the Sentry in guest ring 0; copying only the
   KVM ioctl loop loses the main design advantage.
3. Memory management and syscall dispatch are inseparable. Pointer arguments,
   faults, `mmap`, and `fork` require one coherent memory manager.
4. The Sentry already has the task lifecycle and blocking-syscall behavior that
   Reverie would otherwise need to recreate.
5. KVM does not inherently require root. The process needs permission to open
   `/dev/kvm`; runsc also supports rootless operation. Namespace, mount, and
   network setup may require user namespaces or capabilities depending on the
   deployment.

## Proposed architecture

### Process model

```text
calling application
  |
  +-- reverie-kvm Rust process
  |     - TracerBuilder<T> / Tracer<G>
  |     - Tool, per-process state, per-thread state, global state
  |     - async event reactor
  |     - KvmGuest<T>: Guest<T>
  |
  |       private Unix socket for setup and failure reporting
  |       sealed memfd rings + eventfds for task event traffic
  |
  +-- modified runsc sandbox/Sentry
        - OCI command and stdio setup
        - Reverie interception bridge
        - gVisor task/syscall/signal implementation
        - KVM platform and gofer
              |
            /dev/kvm
```

The Rust process owns trusted tool code. The Sentry owns all virtual Linux
tasks. The application cannot map the control channel. The Sentry applies the
subscription bitmap before creating an event, so unselected syscalls use the
normal gVisor path.

The bridge should be a small patch against gVisor, not a fork of its KVM
platform. It belongs immediately before syscall invocation, after gVisor has
captured the entry registers and applied its own ABI bookkeeping. gVisor's
existing per-syscall feature bitmap in `kernel.SyscallFlagsTable` is a useful
enablement model, but its `External` callback is kernel-wide and cannot replace
the required task-local, request/response state machine.

### Event protocol

Every message starts with a protocol version, sandbox ID, virtual task ID,
monotonic per-task sequence number, and request ID. Lengths and enum tags are
validated before allocation. The initial handshake negotiates architecture,
features, maximum message size, and the syscall subscription bitmap.

Sentry to Rust events:

- `SyscallEnter { number, args, registers }`
- `SignalDelivery { signal, siginfo }`
- `ThreadStart { tid, pid, parent_tid }`
- `PostExec { tid, pid, auxv }`
- `ThreadExit { tid, status }`
- `ProcessExit { pid, status }`
- `InjectedResult { request_id, raw_result }`
- `Fatal { scope, error }`

Rust to Sentry commands:

- `CompleteSyscall { raw_result }`: suppress the pending syscall and install
  the callback result.
- `Inject { number, args }`: execute a syscall through the Sentry syscall table
  in the same virtual task and report its result while keeping the task parked.
- `TailInject { number, args }`: execute the syscall and release the task without
  resuming the Rust callback.
- `ReadMemory`, `WriteMemory`, and `ReadRegisters`.
- `CompleteSignal { deliver: Option<Signal> }`.
- lifecycle acknowledgements and cancellation.

Only one callback may own a task at a time. Different tasks remain concurrent.
The request ID, rather than the vCPU, correlates injected results because a
blocking syscall may deschedule its task and allow another task to use the same
vCPU.

### Syscall callback state machine

1. An application enters a subscribed syscall.
2. The Sentry parks that virtual task and publishes `SyscallEnter`.
3. The Rust reactor locates or creates the process and thread tool state and
   calls `Tool::handle_syscall_event` with `KvmGuest<T>`.
4. `Guest::inject` sends `Inject`. The Sentry invokes the existing syscall
   implementation with interception disabled for that injected call. It can
   block normally. Its result is returned to the same Rust future; the original
   task remains in callback state.
5. If the handler returns, Rust sends `CompleteSyscall` with its value. The
   original syscall is not otherwise executed.
6. `Guest::tail_inject` sends `TailInject`, relinquishes callback ownership, and
   leaves its future permanently pending or explicitly cancelled. This matches
   the current never-return contract.

Successful `execve`, `exit`, and `exit_group` injections do not produce an
`InjectedResult`. They transition through `PostExec` or exit events. Clone and
fork return the virtual child ID and independently create child lifecycle
events. These rules must be implemented in the Sentry state machine rather than
inferred by the Rust frontend.

### Memory and registers

`KvmMemory` implements `reverie_syscalls::MemoryAccess`. Reads and writes are
task-scoped bridge requests handled with the Sentry memory manager's copy-in and
copy-out operations. The Rust process must not walk KVM guest page tables: that
would duplicate fault, copy-on-write, and mapping semantics and would race
Sentry invalidation.

The current `MemoryAccess` API is synchronous. The implementation may use a
dedicated blocking bridge lane per active callback, while the main reactor stays
asynchronous. Shared memory reduces copies for large requests, but the Sentry
must remain the authority that validates the virtual address range.

`Guest::regs` converts gVisor's x86-64 register set to
`libc::user_regs_struct`. `KvmStack` uses the captured user stack pointer and
`KvmMemory`; its allocation and commit behavior can match `GuestStack` without
depending on ptrace.

### Task and tool state

- `GlobalTool` state lives once in the Rust process. `GlobalRPC::send_rpc` is a
  local async call, preserving current centralized-backend behavior.
- `Tool::new` creates process state when the first task in a new virtual thread
  group is announced.
- `Tool::init_thread_state` runs before `handle_thread_start`, using the parent
  state snapshot supplied by the Rust runtime.
- Process state is shared by threads in the virtual thread group. Fork creates
  new process state; clone with shared VM uses the existing process state.
- Exit callbacks run only after the bridge guarantees that no further event can
  arrive for the task or process.

PIDs and TIDs are guest-virtual identifiers. They fit in Reverie's existing
`Pid` representation, but callers must not use them as host PIDs.

### Public backend API

The common path should mirror `reverie-ptrace`:

```rust,ignore
let tracer = reverie_kvm::TracerBuilder::<MyTool>::new(command)
    .config(config)
    .spawn()
    .await?;
let (output, global_state) = tracer.wait_with_output().await?;
```

KVM-specific builder options include the runsc/Sentry binary, root filesystem
policy, network mode, `/dev/kvm` path, and debug logging. `Tracer::guest_pid`
would return the root virtual PID and must be documented as such. A separate
method should expose the host sandbox PID for administration.

`spawn_fn` cannot accept an arbitrary host closure across this process and ABI
boundary. KVM tests should compile small guest executables instead.

## Reverie interface work

### Traits implemented without signature changes

| Interface | KVM implementation |
| --- | --- |
| `GlobalTool` | Runs entirely in Rust. |
| `Tool::subscriptions` | Sent during bridge handshake as a syscall bitmap and instruction feature set. |
| Process/thread constructors | Driven by Sentry lifecycle events. |
| Syscall handler | Driven by the task-local syscall state machine. |
| Signal handler | Driven by a Sentry pre-delivery hook. |
| Post-exec and exit handlers | Driven by explicit Sentry lifecycle events. |
| `Guest::memory` | `KvmMemory` bridge proxy. |
| `Guest::regs` | Captured Sentry register state. |
| `Guest::stack` | `KvmStack` over `KvmMemory`. |
| `Guest::inject` | Sentry syscall-table invocation in the same virtual task. |
| `Guest::tail_inject` | Final Sentry invocation followed by task release. |
| `Guest::daemonize` | Rust backend lifecycle policy; no guest syscall. |
| `GlobalRPC` | Local call to Rust global state. |

### Small core changes required

1. `Guest::auxv` currently reads `/proc/{pid}/auxv` on the host. `Auxv` needs a
   public backend constructor, such as `Auxv::from_entries`, so `KvmGuest` can
   override `auxv` with the Sentry-provided table.
2. Backend capability discovery should be explicit. Add a capability value for
   timers, CPUID, RDTSC, backtraces, GDB, and whether IDs are host IDs. This is
   preferable to discovering unsupported features during a callback.
3. Consider a backend-neutral register type in a later change. Returning
   `libc::user_regs_struct` is usable for the initial x86-64 implementation but
   exposes a ptrace-oriented representation in the common trait.

The `Tool` callback signatures do not need to change.

### Deferred methods

- `set_timer(Time)` can be implemented with a host timer that interrupts the
  virtual task, with documented nondeterminism.
- RCB and instruction-precise timers need KVM PMU support integrated with
  Sentry scheduling. Until then they should return `ENOTSUP`.
- `read_clock` can initially expose a backend monotonic counter; its contract is
  already implementation-specific.
- `backtrace` may return `None` initially. Later it can unwind through
  `KvmMemory` and captured registers.
- gVisor's KVM path already recognizes CPUID faults, but Reverie needs a hook
  before gVisor emulation. RDTSC interception needs separate guest trap support.
  Both should report false/unsupported in the first release.

## Alternatives considered

### Import or port gVisor's KVM package

Rejected. The package depends on Go signal frames, runtime syscall state,
Sentry virtual memory, ring-0 assembly, and internal object layouts. A port
would be a fork of the platform and much of the Sentry, not a KVM wrapper.

### Minimal ring 0 plus host syscall replay

In this design, application `SYSCALL` enters a small guest ring-0 stub and
causes a KVM exit. A host shadow thread then executes the syscall against the
host kernel. It is attractive because it keeps the backend in Rust and may
match native filesystem and network behavior.

It is not recommended for the first backend. Pointer syscalls require guest
memory to be mapped at the same host virtual addresses or copied per syscall.
`mmap`, `clone`, `fork`, `execve`, signals, robust futexes, TLS, rseq,
credentials, and PID semantics all require special synchronization. Unselected
syscalls still need a VM exit because guest ring 3 cannot call the host kernel.
The direct host syscall surface also gives weaker isolation than gVisor.

This option is reasonable only if host-kernel syscall equivalence is a hard
requirement and the project accepts a multi-quarter runtime effort.

### Patched Linux guest kernel

A Linux guest could issue a hypercall from its syscall entry path, receive the
tool decision, and then execute the syscall in the guest kernel. This provides
complete process semantics within the VM and strong isolation, but requires a
maintained guest kernel patch, VM boot/rootfs/device infrastructure, and hooks
for signals and lifecycle events. It also changes command startup and host file
access more substantially than gVisor. Keep it as a fallback if maintaining a
gVisor bridge proves harder than maintaining a kernel patch.

## Isolation and privilege model

The application is untrusted. The Rust tool, Sentry, bridge, and gofer are in
the trusted computing base. The bridge must be reachable only through inherited
sealed fds; no guest-visible socket or filesystem endpoint may expose it.

KVM reduces the application-to-Sentry attack surface relative to direct host
execution, but adds the KVM device and virtualization implementation to the
trusted computing base. gVisor's Sentry remains a large parser and syscall
surface. The Rust process should still sandbox the Sentry using runsc's normal
namespaces, seccomp rules, and gofer separation.

Root is not an intrinsic requirement. Required privileges are:

- read/write permission on `/dev/kvm`;
- permission for the chosen namespace and mount setup, commonly supplied by
  rootless user namespaces or a privileged launcher;
- access to any tap, cgroup, or filesystem resources enabled by configuration.

## Performance expectations

There is no basis yet for promising that this backend is faster than ptrace.

- Unsubscribed syscalls avoid the Reverie bridge but still pay gVisor's normal
  syscall implementation cost.
- A subscribed syscall adds at least one Sentry-to-Rust notification and one
  response. `inject` adds another request/response pair.
- Memory reads from handlers add bridge round trips and may dominate tools such
  as strace.
- ptrace pays host task stops, scheduler transitions, and register operations;
  the shared-memory bridge should avoid some of those costs.

The likely win is sparse subscriptions with light handlers. The default
`Subscription::all_syscalls` may be no faster and can be slower. The prototype
must compare:

1. native Linux;
2. gVisor KVM without Reverie;
3. gVisor KVM with the bridge disabled by subscription;
4. gVisor KVM with a no-op subscribed handler;
5. `reverie-ptrace` with the same subscription and tool.

Measure startup, single-thread syscall latency, throughput, multi-thread
scaling, blocking syscalls, and handler memory access separately.

## Implementation plan

### Phase 0: feasibility spike (2-3 engineer-weeks)

- Pin a gVisor revision and build a KVM-enabled runsc test image.
- Add a single-task syscall entry bridge for x86-64.
- Implement protocol negotiation and a Unix-socket prototype transport.
- Run a Rust tool that observes, suppresses, and replaces `getpid`.
- Demonstrate one repeated `inject` and one memory read.
- Benchmark the round-trip against ptrace and the gVisor baseline.

Decision gate: continue only if callback semantics are correct, KVM works in
the target CI/deployment environment, and measured overhead leaves a credible
path to the project's performance target.

### Phase 1: backend skeleton (4-6 engineer-weeks)

- Create `reverie-kvm` with `TracerBuilder`, `Tracer`, task registry,
  `KvmGuest`, `KvmMemory`, and `KvmStack`.
- Replace the prototype transport with versioned memfd rings and eventfds.
- Translate `reverie_process::Command` into the sandbox launch configuration.
- Support config, global state, subscriptions, stdio, exit status, and tool
  errors.
- Add `Auxv::from_entries` and capability reporting in `reverie`.

### Phase 2: task semantics (6-10 engineer-weeks)

- Implement clone, fork, vfork, exec, thread start, and ordered exit events.
- Implement repeated injection, blocking injection, cancellation, successful
  exec, and never-returning exit syscalls.
- Add signal interception and suppression.
- Validate process/thread/global state behavior against the existing backend.
- Harden disconnect, malformed-message, Sentry-crash, and tool-panic handling.

### Phase 3: packaging and hardening (4-6 engineer-weeks)

- Package the pinned Sentry/runsc artifact and record its source revision.
- Define rootless and privileged deployment profiles.
- Extend Sentry seccomp policy only for the exact bridge fds and operations.
- Add KVM-capable CI, nested-virtualization detection, stress tests, and
  compatibility documentation.
- Run the full applicable Reverie test suite and syscall-heavy benchmarks.

### Phase 4: advanced parity (separate milestones)

- imprecise timers and clock;
- CPUID and RDTSC events;
- PMU/RCB precise timers;
- backtraces;
- aarch64;
- debugger integration.

A useful x86-64 command-execution MVP is approximately 4-6 engineer-months for
engineers familiar with both codebases. Production hardening and advanced
parity are approximately 9-15 engineer-months total. A single engineer should
expect 6-9 months to reach the MVP because the Rust and gVisor work cannot all
proceed independently. Ongoing effort is required to rebase and validate the
pinned gVisor integration.

## Principal risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| gVisor ABI differs from the host | Tools or workloads observe different behavior; unsupported syscalls fail. | State this as a backend semantic, run compatibility tests, and retain ptrace for host-native behavior. |
| gVisor internal API churn | Bridge patches are expensive to rebase. | Keep the patch narrow, pin revisions, add upstream-shaped interfaces, and automate rebase tests. |
| Reentrant callback deadlock | A task can hang during memory access or nested injection. | Use a per-task state machine, independent reactor, request IDs, and deterministic disconnect cancellation. |
| Blocking injected syscalls | A vCPU can run another task while a Rust future awaits the result. | Correlate by virtual task and request ID, never by vCPU or transport lane. |
| Lifecycle ordering | Exec, exit, and clone can invalidate Rust state while callbacks are active. | Emit explicit ordered events from Sentry and model terminal transitions in the protocol. |
| Synchronous memory API | Tool code can block async executor threads and cause excess copying. | Use dedicated blocking lanes now; consider an asynchronous or scoped shared-memory API later. |
| KVM unavailable in CI | Backend coverage becomes optional and regresses. | Provide capability detection and dedicated nested-KVM runners; keep protocol unit tests KVM-independent. |
| Performance misses the goal | IPC and Sentry emulation may exceed ptrace overhead. | Put the benchmark gate in Phase 0 and avoid promising a win before measurement. |
| Tool assumes host PIDs or `/proc` | Correct ptrace tools fail under virtual IDs. | Add capability metadata, override auxv, audit examples, and document virtual identity. |
| Expanded trusted bridge | A malformed event or command can compromise Sentry/tool state. | Sealed inherited fds, fixed maxima, protocol validation, sandbox ID/sequence checks, and fuzzing. |

## Verification strategy

Reuse backend-neutral tests wherever possible, running the same tool against
ptrace and KVM. The first required cases are:

- observe, suppress, replace, inject twice, and tail-inject a syscall;
- read and write pointer arguments and committed stack data;
- blocking syscall while another thread makes progress;
- clone/fork/vfork state initialization;
- successful and failed exec;
- signal delivery and suppression;
- thread/process exit callback ordering;
- tool error, tool panic, Sentry death, and bridge disconnect;
- subscription filtering proving that unselected syscalls publish no event.

Tests that assert host PIDs, host `/proc`, ptrace events, perf-counter timing,
GDB behavior, or `spawn_fn` require backend-specific expectations or remain
ptrace-only until the corresponding feature is implemented.

## Source map

### gVisor

- `pkg/sentry/platform/kvm/kvm.go`: device/VM construction and platform API.
- `pkg/sentry/platform/kvm/machine.go`: vCPU pool and `kvm_run` state.
- `pkg/sentry/platform/kvm/context.go`: task-to-vCPU switch.
- `pkg/sentry/platform/kvm/machine_amd64.go`: syscall/exception vector decode.
- `pkg/sentry/platform/kvm/bluepill*.go`: KVM entry, host exit, and interrupts.
- `pkg/sentry/platform/kvm/address_space.go`: application guest page tables.
- `pkg/ring0/entry_amd64.s`: ring-3 syscall and exception entry.
- `pkg/sentry/kernel/task_run.go`: platform event to task state transition.
- `pkg/sentry/kernel/task_syscall.go`: Linux syscall dispatch.
- `pkg/sentry/kernel/syscalls.go`: per-syscall feature bitmap.

### Reverie

- `reverie/src/tool.rs`: `GlobalTool`, `Tool`, callbacks, and state contracts.
- `reverie/src/guest.rs`: backend-facing `Guest<T>` contract.
- `reverie/src/subscription.rs`: syscall/instruction subscription model.
- `reverie-ptrace/src/tracer.rs`: public builder and current subscription setup.
- `reverie-ptrace/src/task.rs`: callback lifecycle, syscall injection, and the
  current `Guest<T>` implementation.

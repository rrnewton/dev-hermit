# reverie-kvm `Guest` trait audit (vs reverie-ptrace / reverie-dbi)

Status: point-in-time audit. Author: agent `hermit-061`
(task `kvm-m1-guest-trait-audit`). Read-only; no code changed.

Sources (read directly):
- `reverie/reverie/src/guest.rs` — the `Guest<T>` trait definition.
- `reverie/reverie-kvm/src/{lib,vm,memory,syscall,error}.rs` — the KVM crate (520 lines total).
- `reverie/reverie-ptrace/src/task.rs:2100` — `impl<L: Tool> Guest<L> for TracedTask<L>` (reference).
- `reverie/reverie-gdb-finish-fix/reverie-dbi/src/lib.rs:121` — `impl<T> Guest<T> for DbiGuest<'_, T>` (DBI comparison).

Related: `ai_docs/kvm_backend_design.md` (the proposed gVisor-Sentry KVM backend),
`ai_docs/transient/kvm-backend-results.md` (hermit-066: KVM is a vmcall demo, not an
ELF executor), `ai_docs/transient/address-space-architecture.md`.

---

## Headline (and it is the opposite of the hypothesis)

**reverie-kvm implements *none* of the `Guest` trait. There is no `KvmGuest`
type and no `impl Guest for …` anywhere in the crate.** The crate is a bare
KVM building block: a single real-mode vCPU (`KvmBackend`), one page-aligned
guest-physical memory region (`GuestMemory`), and a `vmcall`→host syscall-frame
transport (`SyscallRequest`). It is **not** a Linux process host.

So the task's premise — "KVM may be more complete than DBI since it has a more
natural syscall interception path" — is **not supported by the code**. At the
`Guest`-trait layer the ranking today is:

| Backend | `Guest` impl | Reality |
| --- | --- | --- |
| `reverie-ptrace` | **complete** (`TracedTask`) | production backend |
| `reverie-dbi` | **partial, generic** (`DbiGuest<T>`) | real `memory`/`regs`/`inject`/`read_clock`/`pid`/`tid`/`ppid`; stubs `stack`/`tail_inject`/timers |
| `reverie-kvm` | **none** (no `Guest` type) | VM + memory + vmcall transport only |

**KVM is strictly behind DBI at the Guest-trait layer, not ahead.** The reason
is fundamental, not incidental: KVM gives you a *privilege-transition /
interception mechanism*, but the `Guest` trait models a **Linux process**
(`pid`/`tid`, process-virtual memory, `user_regs_struct`, `inject` of real Linux
syscalls, auxv, stack). The bare KVM prototype runs a hand-built real-mode vCPU
with **no Linux ABI, no process, no virtual address space** — so there is no
substrate for the trait. As `kvm_backend_design.md` concludes: *"KVM can replace
ptrace as the mechanism that transfers execution across a protection boundary,
but KVM alone cannot execute Linux syscalls."* You must first supply a Linux
personality (gVisor's Sentry, or a guest kernel) before `Guest` is implementable.

DBI wins here because DynamoRIO instruments the **real, native Linux guest
process in-process** — so `memory`, `regs`, and `inject` are directly available.
KVM's prototype has thrown that away in exchange for the isolation boundary, and
has not yet rebuilt a Linux ABI behind it.

---

## What reverie-kvm actually provides (the raw primitives)

- `KvmBackend` (`vm.rs`): creates a VM + **one real-mode vCPU** + one memory slot
  at GPA `0x1000`; `install_syscall()` writes a `vmcall/vmmcall; hlt` program and
  a syscall frame; `run(handler)` loops on vCPU exits — `Hypercall` →
  `SyscallRequest` → `handler(&req, &mem) -> i64` → written back to the hypercall
  return; `Hlt` → stop. It internally uses `KVM_GET_REGS`/`KVM_SET_REGS` but does
  **not** expose registers.
- `GuestMemory` (`memory.rs`): an mmap'd, `MAP_SHARED` guest-physical region with
  bounds-checked `read(gpa, buf)` / `write(gpa, buf)`. **Guest-physical, absolute
  addressing** — not process-virtual, and it does **not** implement
  `reverie_syscalls::MemoryAccess`.
- `SyscallRequest` (`syscall.rs`): a `{number, args[6]}` frame marshalled through
  guest memory. There is no result/output-writeback path beyond the handler's
  single `i64`.
- `error.rs`, `lib.rs`: error type + re-exports. `#![cfg(x86_64 + linux)]`.

No `Tool`, no `GlobalTool`, no `GlobalRPC`, no `TracerBuilder`/`Tracer`, no
thread/process registry, no async event loop.

---

## Per-method audit

`Guest<T: Tool>: Send + GlobalRPC<T::GlobalState>` with associated types
`Memory: MemoryAccess + Send` and `Stack: Send + Stack`.

Legend — **KVM status**: every row is **Absent** (no `impl Guest` exists); the
"nearest primitive" column shows what raw material `reverie-kvm` already has.
**Difficulty** assumes you are building `KvmGuest` on top of a Linux-ABI layer
(Sentry/guest-kernel); without that layer, the *architectural* items are blocked
regardless of trait mechanics.

| # | Guest item | Req? | ptrace ref | DBI status | KVM status | Nearest KVM primitive | Difficulty | What it would take |
|---|---|---|---|---|---|---|---|---|
| A1 | `type Memory: MemoryAccess` | yes | remote proc mem | real (`LocalMemory`) | **absent** | `GuestMemory` (GPA r/w) | **Med** | wrap `GuestMemory` as `MemoryAccess`; add guest-VA→GPA page-table translation (identity only in real-mode demo) |
| A2 | `type Stack: Stack` | yes | `GuestStack` | stub (panics) | **absent** | none | **Hard** | needs a real guest stack + ABI (push/reserve/commit into guest VA) |
| 1 | `tid()` | yes | real | real | **absent** | none (single vCPU, no PID) | **Hard** | requires a task/thread model + virtual PID/TID space |
| 2 | `pid()` | yes | real | real | **absent** | none | **Hard** | as above (thread-group identity) |
| 3 | `ppid()` | yes | real | real (may be `None`) | **absent** | none | **Hard** | needs a process tree |
| 4 | `is_main_thread`/`is_root_process`/`is_root_thread` | default | derived | derived | **absent** (defaults need `tid`/`ppid`) | — | Free* | free once 1–3 exist |
| 5 | `auxv()` | default | reads host `/proc/<pid>/auxv` | inherits default | **absent/broken** | none | **Med** | provide Sentry/guest auxv via `Auxv::from_entries` (a proposed core add-on) |
| 6 | `memory()` | yes | remote mem handle | real | **absent** | `GuestMemory` | **Med** | return the `MemoryAccess` wrapper from A1 |
| 7 | `thread_state[_mut]()` | yes | real | real | **absent** | none | **Med** | store `T::ThreadState` in a per-task runtime record (no runtime exists yet) |
| 8 | `regs()` | yes | `PTRACE_GETREGS` | real (DynamoRIO ctx) | **absent** | vCPU `KVM_GET_REGS` (real-mode) | **Med** | expose vCPU regs converted to `user_regs_struct` at a Linux syscall boundary (needs Linux-mode vCPU) |
| 9 | `stack()` | yes | real | stub (panics) | **absent** | none | **Hard** | see A2 |
| 10 | `daemonize()` | yes | real | no-op | **absent** | none | **Easy** | backend lifecycle policy / no-op once a runtime exists |
| 11 | `inject(syscall)` | yes | guest injection | real (DynamoRIO) | **absent** | `run()` handler + vmcall frame | **Hard / architectural** | **the crux**: KVM cannot execute Linux syscalls itself; needs Sentry syscall-table invocation or a guest kernel. Host-side "replay" is rejected in the design doc (pointer args, mmap/clone/exec, signals) |
| 12 | `tail_inject()` | yes | tail injection | stub (panics) | **absent** | none | **Hard** | inject + never-return / task-release semantics |
| 13 | `inject_with_retry()` | default | uses `inject` | default | **absent** (needs `inject`) | — | Free* | free once 11 exists |
| 14 | `into_guest()` | default | default | default | **absent** (blanket) | — | Free* | free (blanket) |
| 15 | `set_timer` / `set_timer_precise` | yes | PMU/RCB | stub (`ENOSYS`) | **absent** | none | **Hard** (or stub) | RCB-precise needs KVM PMU + scheduler; design doc defers to `ENOTSUP`. Can stub `ENOSYS` cheaply |
| 16 | `read_clock()` | yes | RCB counter | real (branch count) | **absent** | none (no counter) | **Med** | expose a backend monotonic/branch counter (design doc: allowed to be impl-specific) |
| 17 | `backtrace()` | default | real | default (`None`) | **absent** (default) | — | Free* / Med | `None` initially; later unwind via memory+regs |
| 18 | `has_cpuid_interception()` | default | real | default | **absent** (default) | — | Free* / Med | report false initially; CPUID-fault hook is a later add |
| R1 | `GlobalRPC::send_rpc()` (supertrait) | yes | in-proc + bincode | in-proc shortcut | **absent** | none | **Med** | local async call if global state is in the Rust process (design doc); cross-process if multi-VM |
| R2 | `GlobalRPC::config()` (supertrait) | yes | real | real | **absent** | none | **Easy** | return `&T::Config` from a runtime |

`*Free` = falls out of the trait's default body once its dependencies exist; no
KVM-specific code needed.

**Score: 0 of 14 required methods, 0 of 2 associated types, 0 of 2 GlobalRPC
supertrait methods implemented.** (DBI, by contrast, implements ~7 of the
required methods for real and stubs the rest, on a generic `DbiGuest<T>`.)

---

## Roadmap implications

1. **KVM is not the faster path to a working `Guest` today.** DBI already has a
   generic `DbiGuest<T>` with real `memory`/`regs`/`inject`; its main blocker is
   the executor/hosting/RPC plumbing (being addressed under
   `dbi_wire_client_syscall`). KVM has **no** `Guest` type and a much deeper
   prerequisite.
2. **The KVM prerequisite is a Linux ABI, not trait plumbing.** Per
   `kvm_backend_design.md`, the recommended path is the **gVisor-Sentry bridge**:
   run the tool + global state in a Rust process and implement `KvmGuest<T>` as a
   proxy over a Sentry bridge (memory copy-in/out, captured registers, Sentry
   syscall-table `inject`). That is a ~6–9 engineer-month MVP. A standalone
   host-syscall-replay VMM is explicitly **not** recommended (it would rebuild a
   second Linux personality).
3. **Small core add-ons the design already anticipates** (also relevant to any
   non-ptrace backend): `Auxv::from_entries` (row 5), explicit backend
   capability discovery (timers/CPUID/RDTSC/host-vs-virtual IDs), and eventually
   a backend-neutral register type instead of `libc::user_regs_struct` (row 8).
4. **If KVM work proceeds, sequence it as:** (a) pick the Linux-ABI substrate
   (Sentry bridge); (b) stand up a `reverie-kvm::TracerBuilder<T>`/`Tracer` +
   task registry (unlocks rows 1–4,7,10,13,14,R2); (c) `KvmMemory: MemoryAccess`
   over the bridge (A1,6) + `regs` (8); (d) the syscall state machine for
   `inject`/`tail_inject` (11,12); (e) defer timers/CPUID/backtrace/stack
   (15,17,18,A2) behind capability flags.

### Bottom line

Honest answer to "will KVM yield fruit faster than DBI?": **not at the
`Guest`-trait level.** DBI is materially ahead (a real, generic `Guest` impl vs
none). KVM's interception mechanism is real and works as a demo, but the
`Guest` trait needs a Linux process behind it, and the bare KVM prototype does
not provide one. KVM's payoff is a longer, Sentry-sized investment; its upside
(per the design doc and hermit-062's benchmarks) is syscall-bound server
workloads, not a quicker route to trait completeness.

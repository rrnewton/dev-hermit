# gVisor KVM architecture → hermit/reverie mapping

Status: research + implementation plan. Author: agent `hermit-061`
(task `research-gvisor-kvm-architecture`). Based on a shallow clone of
`github.com/google/gvisor` at commit `648dfe1` (2026-07-23), read directly.

Builds on: `ai_docs/kvm_backend_design.md` (the original gVisor-KVM study, pinned
to gVisor `ae10899a`, with the VM-exit mechanics + source map),
`ai_docs/transient/kvm-guest-trait-audit.md` (M1: reverie-kvm implements no
Guest), reverie PR #25 (M2: minimal `KvmGuest`), and
`ai_docs/transient/kvm-m3-detcore-integration-status.md` (M3: Detcore-on-KVM is
blocked on the missing Linux-ABI substrate).

---

## TL;DR — what gVisor actually is, and the one correction that matters

gVisor is two cleanly separated things:

1. **A Platform** (`pkg/sentry/platform/`) — the mechanism that transfers a
   thread across the user/kernel protection boundary and reports why it stopped.
   KVM is one implementation (`pkg/sentry/platform/kvm/`); ptrace is another.
   **This is small and generic.** `reverie-kvm` is already a sliver of it.
2. **The Sentry** (`pkg/sentry/kernel/`, `mm/`, `vfs2/`, `loader/`, hundreds of
   syscall impls) — a **userspace Linux kernel**: it *implements* the Linux ABI
   (syscalls, virtual memory, signals, process/thread lifecycle, ELF loading).
   **This is enormous** (the bulk of gVisor's ~600k LOC).

**The correction to "gvisor Sentry → hermit Detcore":** the Sentry is **not**
Detcore's analog. The Sentry is the **Linux ABI / kernel personality** — exactly
the substrate hermit's KVM path *lacks* (the M3 blocker). Detcore is an
*instrumentation/determinism layer that sits around syscall dispatch* — its
closest gVisor analog is the per-syscall **hook** (`SyscallTable.External` / the
point in `Task.doSyscall`), **not** the SyscallTable's implementations.

So "copy gvisor" has two very different readings, and only one is fast:

- **Reuse the Sentry as a dependency** (run the app behind a gVisor sandbox,
  hook the syscall boundary, keep Detcore out-of-process). This is the
  `kvm_backend_design.md` recommendation and the *only* path that is
  quarters-not-years. **Recommended.**
- **Reimplement the Sentry in Rust** (a second Linux personality). This is the
  6–9+ engineer-month effort M1–M3 kept pointing at. "Copying gvisor" in the
  sense of *porting* it is not fast; the fast path is *reusing* it.

`reverie-kvm` maps to gVisor's **Platform** layer (the easy, already-started
part). The hard part gVisor gives you for free is the **Sentry**, which hermit
has no analog of — so the leverage is in *reusing* it, not rebuilding it.

---

## gVisor architecture (layered), with citations

```
        guest ring 3: application (untrusted)
              |  SYSCALL / fault / interrupt
              v
   pkg/ring0/entry_amd64.s        (ring-0 sysenter/exception entry; saves regs)
              |
   pkg/sentry/platform/<impl>     (Platform: Context.Switch transfers control)
     - kvm/   : one shared KVM VM; Sentry at guest ring 0, app at guest ring 3
     - ptrace/: host ptrace
              |  Switch() returns: nil=syscall | ErrContextSignal | Interrupt | CPUPreempted
              v
   pkg/sentry/kernel/task_run.go  (Task.runApp: interprets the Switch result)
              |  nil -> doSyscall
              v
   pkg/sentry/kernel/task_syscall.go (doSyscall -> executeSyscall)
              |  SyscallTable.Lookup(sysno) -> SyscallFn
              v
   pkg/sentry/kernel/ + mm/ + vfs2/ + ...  (THE SENTRY: implements the syscall)
```

### 1. The Platform abstraction — `pkg/sentry/platform/platform.go`

```go
type Platform interface {
    NewAddressSpace(...) (AddressSpace, error)  // a guest address space
    NewContext(context.Context) Context         // an execution context (a thread)
    DetectsCPUPreemption() bool; PreemptAllCPUs() error; NumCPUs() int; ...
}

type Context interface {
    // nil => the context invoked a syscall; ErrContextSignal => a signal;
    // ErrContextInterrupt; ErrContextCPUPreempted.
    Switch(ctx, mm MemoryManager, ac *arch.Context64, cpu int32)
        (*linux.SignalInfo, hostarch.AccessType, error)
}

type AddressSpace interface {
    MapFile(addr, f memmap.File, fr FileRange, at AccessType, precommit bool) error
    Unmap(addr, length); Release(); PreFork(); PostFork()
    // AddressSpaceIO: CopyOut / CopyIn / ZeroOut (remote memory access)
}
```

`Context.Switch` **returning `nil` == "a syscall was intercepted"** is the whole
interception model in one line. It is exactly what `reverie-kvm`'s
`KvmBackend::run` loop already does (VM-exit on `vmcall` → a syscall event).

### 2. KVM platform specifics — `pkg/sentry/platform/kvm/`

- One KVM VM per sandbox; the Sentry runs at **guest ring 0** and the app at
  **guest ring 3**, sharing the VM (not a whole-machine VMM). `machine.go`
  builds a pool of vCPUs; `address_space*.go` builds per-app guest page tables;
  `physical_map*.go` keeps an injective host-virtual→guest-physical map.
- App syscall fast path: `ring0/entry_amd64.s` sysenter saves regs → the
  platform returns from `SwitchToUser` with a `ring0.Syscall` vector → `Switch`
  returns `nil` → `runApp` → `doSyscall`. No host VM-exit for app syscalls.
- Sentry's *own* host syscalls: `bluepill_amd64.go:KernelSyscall` rewinds the IP
  and executes `HLT` → a KVM exit → `bluepillHandler` runs `KVM_RUN` and lets the
  syscall execute from host ring 3, then resumes. (Comments at
  `bluepill_amd64.go:103-131`.) This is how a userspace kernel makes real host
  syscalls without a VM-exit per *app* syscall.

### 3. Syscall dispatch — `pkg/sentry/kernel/task_syscall.go`, `syscalls.go`

```go
func (t *Task) executeSyscall(sysno, args) (rval, ctrl, err) {
    s := t.SyscallTable()
    ...
    fn := s.Lookup(sysno)          // SyscallFn for this number
    if fn != nil { rval, err = fn(t, sysno, args) }   // the Sentry's impl
    else         { rval, err = s.Missing(t, sysno, args) }
}

type SyscallFn func(t *Task, sysno uintptr, args arch.SyscallArguments)
    (uintptr, *SyscallControl, error)
type SyscallTable struct { Table map[uintptr]Syscall; External ...; Missing ... }
```

`SyscallTable.Table` **is** the Linux kernel personality — one `SyscallFn` per
syscall, implemented against the Sentry's MM/VFS/task model. `External` is a
kernel-wide pre-hook. This is the substrate hermit lacks; the `External`/pre-hook
site is the shape of a Reverie/Detcore interception point.

### 4. ELF loading — `pkg/sentry/loader/loader.go` (+ `elf.go`, `interpreter.go`, `vdso.go`)

```go
func Load(ctx, args LoadArgs, extraAuxv, vdso) (ImageInfo, ...)
  -> loadExecutable(...)   // parse ELF, map PT_LOAD segments, resolve PT_INTERP
  -> allocStack(...)       // MapStack into the address space
  -> push argv, envp, and auxv (AT_RANDOM (16 random bytes), AT_EXECFN,
     AT_UID/EUID/GID/EGID, AT_ENTRY, AT_PHDR, ...)  // sets up the initial stack
  -> load the VDSO
```

hermit's ptrace path never needs this (the host kernel's `execve` loads the ELF).
The KVM path has **no loader at all** — this whole file is the concrete thing a
KVM backend must provide (or delegate to the Sentry).

---

## The mapping table

| gVisor concept | File(s) | hermit/reverie analog | Status today | Notes |
| --- | --- | --- | --- | --- |
| **Platform** interface | `platform/platform.go` | `reverie-kvm::KvmBackend` (+ the implicit Reverie backend contract) | Partial | reverie-kvm is a 1-vCPU sliver of Platform; no `NewAddressSpace`/multi-vCPU |
| **Context.Switch → nil=syscall** | `platform/platform.go`, `platform/kvm/` | `KvmBackend::run`/`run_tool` loop (VM-exit on `vmcall`) | Works (demo) | same "exit == syscall event" model; M2 drives a Tool from it |
| **AddressSpace** (MapFile/Unmap/CopyIn/Out) | `platform/platform.go`, `kvm/address_space*.go` | `reverie-kvm::GuestMemory` + `KvmGuest::memory` (`MemoryAccess`) | Minimal | GuestMemory = one GPA region, identity VA=GPA (real mode); no page tables |
| **ring0 entry / SwitchToUser** | `ring0/entry_amd64.s`, `kvm/machine_amd64.go` | reverie-kvm real-mode `vmcall;hlt` stub | Prototype | gVisor runs real protected-mode ring3; reverie-kvm is real-mode toy |
| **Sentry host-syscall (bluepill)** | `kvm/bluepill*.go` | none | Missing | needed only if a Rust "sentry" makes host syscalls from ring 0 |
| **SyscallTable (the Linux ABI impls)** | `kernel/syscalls.go`, `kernel/task_syscall.go`, `mm/`, `vfs2/` | **none** (this is the M3 gap) | **Missing** | the userspace-kernel; reuse gVisor's or a guest kernel — do NOT reimplement |
| **Per-syscall hook** (`SyscallTable.External`, `Task.doSyscall` site) | `kernel/task_syscall.go` | **Reverie Tool hook** = `Tool::handle_syscall_event` (and Detcore as the tool) | Exists (as a trait) | THIS is Detcore's analog — instrumentation around dispatch, not the impls |
| **Task / thread & process model, virtual PIDs** | `kernel/task*.go`, `kernel/thread_group.go` | Detcore's own task model (ptrace) / **none on KVM** | Missing on KVM | M1 flagged KvmGuest pid/tid as synthetic |
| **ELF loader + stack/auxv setup** | `loader/loader.go`, `elf.go`, `vdso.go` | **none** | **Missing** | KVM path has no loader; ptrace relies on host `execve` |
| **`runApp` event loop** (Switch → syscall/signal/preempt) | `kernel/task_run.go` | `reverie-ptrace` `TracedTask` loop; KVM `run_tool` (minimal) | Partial | KVM needs a real async (tokio) loop — see M3 |
| **Signal delivery** (`ErrContextSignal`) | `kernel/task_run.go`, platform | reverie `handle_signal_event` | Trait exists; KVM none | |
| **CPU-preemption detection** (`DetectsCPUPreemption`) | `platform.go`, kvm | Detcore RCB/PMU preemption | Different mechanism | gVisor uses vCPU preemption; Detcore uses RCB counts |

### The clean three-layer correspondence

```
 gVisor:   [ KVM Platform ] → [ Sentry: SyscallTable + MM + VFS + loader ] → [ (hooks) ]
 hermit:   [ reverie-kvm  ] → [   *** MISSING: Linux-ABI substrate ***    ] → [ Detcore (Tool) ]
                ^ have (sliver)          ^ the gap (reuse gVisor Sentry)         ^ have
```

Detcore already occupies the right-most box (a Reverie `Tool`). reverie-kvm
occupies a sliver of the left box. The entire middle box is missing — and it is
the largest part of gVisor.

---

## Copy vs adapt vs reuse

- **Directly copyable (shapes/designs, small):**
  - The Platform/Context/AddressSpace *interface shape* — especially
    "`Switch` returns nil ⇒ syscall." reverie-kvm already matches it; formalize
    `KvmBackend` toward `NewAddressSpace`/`NewContext`/multi-vCPU.
  - The ELF-load sequence (`loadExecutable` → `allocStack` → push argv/envp/auxv
    incl. `AT_RANDOM`/`AT_EXECFN`/`AT_PHDR`, VDSO). This is well-specified and
    portable; it is concrete, bounded work.
  - The `runApp` dispatch skeleton (Switch → {syscall, signal, preempt}).
- **Adapt (Go→Rust + Reverie idioms):**
  - Async run loop: gVisor is goroutine-per-task; hermit needs a tokio
    `LocalSet` loop that also services Detcore's scheduler (the M3 blocker).
  - Interception point: gVisor's `SyscallTable.External`/`doSyscall` hook →
    Reverie `Tool::handle_syscall_event`; keep Detcore out-of-process (or in a
    trusted Rust process), reached by RPC — matches
    `kvm_backend_design.md`'s bridge.
  - Virtual PID/TID + task/thread tree (gVisor `ThreadGroup`/`Task`).
- **Reuse as a dependency (do NOT reimplement):**
  - **The Sentry itself** — the SyscallTable implementations, MM, VFS, signals,
    process lifecycle. Run the app inside a (patched) gVisor sandbox and add a
    narrow bridge at the syscall boundary that forwards selected syscalls to the
    Rust tool. This is the only path that is quarters, not years.

---

## Implementation plan (gVisor-shaped, reconciled with M1–M3)

This supersedes "wire Detcore onto the bare prototype" (M3 showed that is blocked
top-to-bottom). The gVisor model says the order is:

- **Phase 0 — decision + spike (2–3 wks):** confirm the *bridge* approach (reuse
  gVisor Sentry) vs a standalone Rust VMM. Build a single-syscall Sentry-bridge
  spike (patch `Task.doSyscall`/`External` to forward `getpid` to a Rust process
  over a socket, get a decision back). Mirrors `kvm_backend_design.md` Phase 0.
- **Phase 1 — Platform parity in reverie-kvm (4–6 wks):** grow `KvmBackend`
  toward the Platform interface: `NewAddressSpace`, protected-mode ring3 (not
  real mode), per-app page tables, a real async `run` loop (tokio `LocalSet`)
  whose "exit == syscall" feeds a Reverie `Tool`. (Unblocks M3's tokio issue.)
- **Phase 2 — Linux ABI substrate (the gate):** either (a) integrate gVisor's
  Sentry via the bridge (recommended — reuse `loader/`, `kernel/SyscallTable`,
  `mm/`), or (b) begin a Rust guest-kernel (not recommended; years). Only after
  this can `hermit run --backend kvm -- /bin/echo` run *anything*.
- **Phase 3 — Detcore on top:** route the bridge's syscall events through a
  tokio-hosted `Detcore` (like ptrace's `TracerBuilder::<Detcore>`), with a
  concurrent scheduler and (later) cross-process `GlobalRPC`. Then `--strict`
  and `--verify` become meaningful. Land reverie PR #25 + re-pin hermit here.
- **Phase 4 — parity:** virtual PIDs, signals, timers (map gVisor vCPU
  preemption vs Detcore RCB), CPUID/RDTSC, multi-thread.

### Honest bottom line

gVisor confirms the shape M1–M3 inferred and de-risks the *design*: Platform =
vehicle (reverie-kvm), Sentry = Linux ABI (the gap), syscall-hook = the Reverie
Tool (Detcore). But "copy gvisor" is fast **only** if it means *reuse the Sentry*
as the ABI substrate; reimplementing the Sentry in Rust is the multi-quarter path
the earlier milestones kept hitting. The next concrete milestone should be the
**Phase 0 Sentry-bridge spike**, not another attempt to wire Detcore onto the
bare real-mode prototype.

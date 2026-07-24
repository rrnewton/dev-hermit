# Hermit Architecture Overview

Status: current architecture and backend boundary as of 2026-07-21.

Hermit makes a Linux process tree reproducible by controlling the events where
it can observe or influence nondeterminism. The production path combines the
Hermit CLI, Detcore, and Reverie's ptrace backend. SaBRe and KVM are separate
backend experiments with materially different capability sets; neither is a
drop-in production replacement.

## Component map

```text
user command
    |
    v
hermit CLI and container setup
    |
    v
Reverie execution backend <---- Detcore implements reverie::Tool
    |                                  |
    | event callbacks                  | deterministic policy
    v                                  v
guest process tree              scheduler and global state
    |                                  |
    +---- Linux syscalls --------------+
```

The repositories have distinct roles:

- `rrnewton/hermit` contains `hermit-cli`, Detcore, schedule models, tests,
  record/replay support, and the user-facing binary.
- `rrnewton/reverie` contains process execution, tracing, the shared `Tool` and
  `Guest` interfaces, and experimental backend work.
- `rrnewton/dev-hermit` pins both repositories and stores durable research,
  experiments, and isolated worktree coordination.

## Production execution flow

1. `hermit run` parses execution and determinism options.
2. The CLI normally creates user, PID, UTS, and mount namespaces. It constructs
   `/proc`, an isolated `/tmp`, and optionally a network namespace and `sysfs`.
3. `hermit-cli/src/lib.rs` creates a
   `reverie_ptrace::TracerBuilder<Detcore>` for the command.
4. Reverie starts the tracee and delivers subscribed events to Detcore.
5. Detcore either emulates an operation, injects it into Linux under controlled
   scheduling, or passes it through according to the selected mode.
6. Per-thread Detcore state communicates with a serialized global scheduler
   through Reverie's `GlobalRPC` interface.
7. Hermit returns guest output/status and, when requested, schedules,
   summaries, recordings, or verification results.

The namespace wrapper improves isolation and repeatability but is not the
interception mechanism. Source and runtime audits proved that the core
parent-child ptrace and user-only PMU path can operate without namespace or
mount capabilities. See [container-deployment.md](container-deployment.md).

## Interception model

Detcore subscribes only to events required by its configuration. The ptrace
backend can deliver:

- syscall entry/exit and injected-syscall results;
- thread start, exec, exit, and signal events;
- CPUID events;
- RDTSC and RDTSCP events;
- timer events driven by retired conditional branch (RCB) counters;
- guest register, memory, stack, and backtrace access through `Guest`.

Syscalls are not all handled alike. A dedicated Detcore arm can model logical
resources, blocking, virtual metadata, time, and record/replay data. An
explicit passthrough intentionally exposes Linux behavior. A fallback injects
an otherwise unsupported syscall in permissive mode and fails under
`--panic-on-unsupported-syscalls`. A dedicated arm therefore does not, by
itself, prove complete deterministic semantics for every flag or mode.

RDTSC interception is x86-specific. Reverie sets
`PR_SET_TSC/PR_TSC_SIGSEGV`; Linux then faults real RDTSC/RDTSCP instructions.
Reverie decodes the instruction at RIP, asks Detcore for a synthetic value,
writes the result registers, advances RIP, and suppresses that SIGSEGV. The
mechanism applies to normal text, shared libraries, and JIT code that executes
a real instruction.

## Deterministic state and scheduling

Detcore has two main halves:

- `tool_local` owns process/thread-near event handling and `ThreadState`.
- `tool_global` owns `GlobalState`, resource arbitration, logical time, and
  the central scheduler.

The normal deterministic mode serializes runnable guest threads and selects
the next thread deterministically. PMU timers count user-space retired
conditional branches so Hermit can preempt CPU-bound code at replayable
instruction regions. Syscall, CPUID, RDTSC, signal, and scheduling events can
be recorded into a normalized schedule and replayed in a synthetic total
order.

Hermit controls observable process events, not every environmental input. A
changing host filesystem, external network peer, unsupported device, or real
parallel weak-memory behavior remains outside the core model unless a higher
layer freezes or records it.

## Backend status

| Backend | Interception boundary | Linux semantics | Status | Main tradeoff |
| --- | --- | --- | --- | --- |
| Reverie ptrace | Host ptrace stops, seccomp, signals, PMU timers | Host kernel | Production baseline | Broad capability, but syscall/thread stops and precise PMU tails are expensive |
| Reverie SaBRe | In-process binary rewriting and callbacks | Host kernel | Experimental draft | Fast narrow syscall path, but incomplete lifecycle, signals, generated code, and shared API parity |
| Reverie KVM/Sentry | Application ring 3 to gVisor Sentry ring 0, bridged to Rust tools | gVisor Linux ABI | Protocol skeleton/research proposal | Strong boundary and potential speed, but requires a Sentry bridge and accepts ABI differences |

### Ptrace baseline

This is the only backend used by Hermit today. It implements the shared async
`reverie::Tool`, `Guest`, and `GlobalTool` model used by Detcore. It supports
the event and inspection surface required by precise scheduling, schedule
search, signals, process lifecycle, injected syscalls, and stack reports.

Measured overhead has two independent sources. Minimal ptrace still costs
roughly 10x on syscall/thread microbenchmarks. A CPU-only benchmark reached
roughly 40x because precise AMD PMU preemption generated about 1.9 million
context switches and long skid-correction tails. A faster syscall backend does
not solve PMU calibration and preemption overhead.

### SaBRe

The restored SaBRe stack is a real x86-64 in-process interceptor. A narrow
single-threaded `getpid` probe was about 13x faster than ptrace, and the
historical `riptrace` path can trace basic commands, exec, fork/wait, and exit
status after restoration work.

It is not API-compatible with ptrace. SaBRe defines a synchronous, process-local
tool API and separate RPC system. Shared Reverie tools cannot switch backends
by recompiling. Signal replacement, clone/vfork/exec continuity, abrupt exits,
register/stack access, PMU timers, CPUID/RDTSCP, static/JIT coverage, and
backend-neutral conformance remain incomplete. Treat it first as a bounded
syscall-centric backend extension, not as parity.

### KVM/Sentry

KVM supplies an execution boundary, not a Linux syscall implementation. The
design follows gVisor: application `SYSCALL` enters guest ring 0, and the
Sentry implements process, memory, signal, futex, and syscall behavior. A Rust
`reverie-kvm` frontend would proxy the existing tool interface over a narrow,
versioned bridge.

The current code is a protocol skeleton with version, architecture,
capability, and message-bound negotiation. The Sentry bridge is not
implemented. A standalone KVM ioctl loop would require building another Linux
personality and is not the smaller path. The planning estimate remains 4-6
engineer-months for an x86-64 MVP and 9-15 months for broader parity and
hardening.

## QEMU is a workload, not a backend

Running QEMU/TCG under Hermit keeps the ptrace backend outside QEMU. Hermit
controls the host QEMU process and its threads; QEMU translates guest CPU
instructions and emulates guest devices. That arrangement is the current path
toward deterministic Linux-kernel experiments. It is distinct from a Reverie
KVM/Sentry backend, which would execute an ordinary application against a
user-space Linux ABI. See [qemu-integration-status.md](qemu-integration-status.md).

## Design constraints

- Production support is x86-64 Linux. Aarch64 paths are incomplete.
- PMU/RCB scheduling is hardware and host-policy dependent.
- Backend claims require common lifecycle, signal, memory, injection, and
  workload conformance tests, not only build success.
- Determinism claims must state filesystem, network, namespace, CPU, and PMU
  assumptions.
- Exact load/store race localization needs memory/instruction instrumentation
  beyond the current event schedule.

## Further reading

- [hermit-v2-roadmap.md](hermit-v2-roadmap.md)
- [sabre_backend_assessment.md](sabre_backend_assessment.md)
- [kvm_backend_design.md](kvm_backend_design.md)
- [schedule-search-guide.md](schedule-search-guide.md)

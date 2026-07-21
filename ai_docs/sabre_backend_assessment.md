# Reverie SaBRe backend assessment

The `experimental/reverie-sabre` directory and its companion crates were
restored from commit `374247cc`, the last commit before `407899245d` removed
the experimental in-guest interception stack. The original public import was
`c6532de85d96530b55732a619b9e89b01742d5f0` (D41099658).

This assessment was performed on x86-64 with Rust 1.99.0-nightly
(2026-07-11).

## Build status

The restored packages have been added to the root Cargo workspace. All nine
packages pass `cargo check` after two source compatibility updates:

- The two inline assembly labels named `1` were renamed because current LLVM
  rejects binary-looking labels.
- `riptrace` was moved from Clap 3 to the repository's existing Clap 4
  dependency. Clap 3 selected `rustix 0.37`, which does not compile with the
  current nightly.

The packages that check are:

- `nostd-print`
- `reverie-rpc-macros`
- `reverie-rpc`
- `reverie-sabre-macros`
- `reverie-sabre`
- `reverie-host`
- `riptrace-rpc`
- `riptrace-tool`
- `main` (the `riptrace` command)

The support crates and `riptrace` command also pass their test and doc-test
builds. They currently contain no tests.

`reverie-sabre`'s unit test binary does not link:

```text
rust-lld: error: undefined symbol: vfork_syscall
```

Both native sources under `src/ffi` are dangling symlinks into the original
monorepo:

- `recursion_protector.c` points to
  `third-party/sabre/plugin_api/recursion_protector.c`.
- `vfork_syscall.S` points to
  `third-party/sabre/plugin_api/arch/x86_64/vfork_syscall.s`.

The MIT-licensed source files still exist in the public SaBRe repository at
<https://github.com/srg-imperial/SaBRe>, but neither that repository nor a
revision of it is declared by this project. The generated Cargo manifests also
have no build script to compile those files.

A full workspace check currently fails earlier in an unrelated existing
`reverie-ptrace` dependency: `procfs 0.15.1` selects `rustix 0.36.17`,
which has the same current-nightly incompatibility. Package-scoped checks were
used to distinguish that failure from the restored stack.

## What is implemented

The code is a substantial x86-64 in-process interceptor, not a sketch. It
contains:

- SaBRe callbacks for syscalls, RDTSC, VDSO functions, and named function
  detours.
- Special handling for clone, clone3, vfork, exec, exit, and exit_group.
- Thread registration, exit coordination, and a signal virtualization layer.
- Direct access to guest memory through `LocalMemory`.
- A generated synchronous RPC client/service protocol over a Unix socket for
  process-global state.
- A host launcher that locates a SaBRe binary and plugin, starts the RPC server,
  and runs the target through SaBRe.
- `riptrace`, a host command and injected tool that demonstrate the full
  intended architecture.

## What is not runnable

The public Cargo build cannot currently produce an end-to-end tracer:

1. The external SaBRe loader is not built, pinned, or distributed.
2. The native plugin API sources are dangling symlinks and are not compiled by
   Cargo.
3. `riptrace-tool` is built as an `rlib`, not the `cdylib` that the SaBRe
   loader expects.
4. Its example `malloc` and `free` detours call `todo!()`; any intercepted
   allocation would panic.
5. There are no end-to-end tests for exec, fork/clone, signals, VDSO calls, or
   multithreaded programs.
6. The implementation contains unresolved correctness notes, including an
   exec path race and an explicitly documented undefined-behavior workaround
   in thread state setup.

The public SaBRe runtime itself requires CMake, Make, and GCC. Its source is
available, but CMake was not installed in the assessment environment, so the
external loader was not built here.

## Interface compatibility

SaBRe does not implement the shared `reverie::Tool` and `reverie::Guest`
interfaces used by `reverie-ptrace`. It defines a separate synchronous
`reverie_sabre::Tool` trait and a separate `#[reverie_sabre::tool]` macro.
This was already true at the last historical revision; current changes to the
shared tool trait are not the cause.

| Capability | `reverie-ptrace` / shared API | `reverie-sabre` |
| --- | --- | --- |
| Tool entry point | Async `reverie::Tool` handlers over a `Guest` | Synchronous, in-process `Tool::syscall` |
| Global state | `GlobalTool` with typed async RPC and config | Separate generated blocking RPC client/service |
| Process state | One `Tool` value per process | One macro-generated singleton per process |
| Thread state | Typed `ThreadState`, parent-aware initialization, serialization | Internal runtime thread records; tools only receive start/exit IDs |
| Syscall execution | `Guest::inject`, `tail_inject`, and retry support | Direct syscall execution through `SyscallExt::call` |
| Guest inspection | PID/TID/PPID, registers, stack, auxv, memory, backtrace | Local memory and thread ID only |
| Event selection | `Subscription` filters | No shared subscription contract |
| Signals | Async handler can replace or suppress delivery | Notification callback returns no delivery decision |
| CPU events | CPUID and RDTSC/RDTSCP | RDTSC only |
| Other events | post-exec, timers, daemonization, typed exit status | No equivalents; adds VDSO and named-function detours |
| Launch result | `Tracer` exposes PID, wait, output, and global state | Returns `reverie_process::Child` |
| Architecture | Linux x86-64 and aarch64 paths | Unconditionally x86-64 assembly and intrinsics |

Tools written for ptrace therefore cannot be recompiled against Sabre by
changing only the backend. Even the syscall return types differ
(`Result<i64, reverie::Error>` versus `Result<usize, syscalls::Errno>`).

## Work required

A historical `riptrace` proof of concept is relatively bounded:

1. Pin and build a compatible public SaBRe revision.
2. Vendor or otherwise provide the two MIT plugin API sources.
3. Add Cargo native compilation and `cdylib` packaging.
4. Remove or implement the unfinished allocation detours.
5. Add smoke coverage for simple commands, exec, clone/vfork, signals, and
   multithreaded processes.

Estimate: 1 to 2 engineer-weeks, with modern glibc/loader compatibility as the
main uncertainty.

Making Sabre a syscall-only backend for the shared tool API requires a new
in-process `Guest` adapter, shared global-state RPC/config plumbing, real tool
thread-state storage, async-handler execution from synchronous callbacks, and
lifecycle/subscription translation.

Estimate: another 4 to 6 engineer-weeks for a constrained x86-64 prototype.

Credible interchangeability with `reverie-ptrace` also requires defined
behavior for registers and stacks, signal suppression, post-exec, CPUID,
timers, exit status, output/global-state return, architecture gating, and a
backend-neutral conformance suite. Some features may require changes to SaBRe
itself rather than an adapter.

Estimate: 8 to 12 or more engineer-weeks total, followed by workload-specific
stabilization. The restored code is useful as a design reference for fast
in-guest interception, but it is not currently close to backend parity.

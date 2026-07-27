# Loading pods into existing programs

`shmem-pod` defines shared state and executable image contracts. Injection is a
separate bootstrap layer: it gets a small trusted adapter into a process, maps
the pod's code and state, validates them, and installs call sites that pass the
state base to the pod's C ABI methods.

The target program does not need to use Rust or know Rust's ABI. The durable
boundary is an `extern "C"` entry table plus an explicit per-process context:

```text
process-local context
  code_base  -> authenticated RX image
  state_base -> shared RW mapping
  methods    -> validated offsets within code_base
```

## `LD_PRELOAD` at exec time

A preload DSO is the simplest integration for a dynamically linked, non-secure
executable:

1. Its constructor or first interposed call receives inherited code/state file
   descriptors or names through a protected bootstrap channel.
2. It maps code `PROT_READ | PROT_EXEC` and state `MAP_SHARED` with
   `PROT_READ | PROT_WRITE`.
3. It verifies the artifact hash, image header, ABI signatures, state
   generation, layout fingerprint, and any required fixed virtual address.
4. Interposed libc functions call the original function, preserve `errno`, and
   invoke a pod method through the validated context.

This requires no changes to the guest binary, but it does not cover static
binaries, direct system calls, `AT_SECURE` programs, or symbols that bypass ELF
interposition. Loader policy may also ignore or sanitize `LD_PRELOAD`.

## `ptrace` bootstrap and detach

A ptracer can attach after exec and leave no supervisor on the steady-state
call path. A practical injector must:

1. Seize and stop every thread, accounting for threads created during attach.
2. Save registers and arrange remote syscalls or a small temporary bootstrap.
3. Transfer sealed code and shared-state descriptors. `SCM_RIGHTS` over a Unix
   socket is explicit and auditable; reopening `/proc/<injector>/fd/<n>` is
   simpler but subject to ptrace, procfs, namespace, and LSM policy.
4. Remote-map the image RX and state RW. Fixed-address pod values additionally
   require `MAP_FIXED_NOREPLACE` at the authenticated address.
5. Write and initialize the process-local context and hook trampolines.
6. Patch selected call sites or dispatch tables while all threads are stopped,
   restore state, and detach.

Mappings and code patches survive `PTRACE_DETACH`. Ptrace only bootstraps the
adapter; it does not itself create a recurring hook. A remote `dlopen` can load
a normal DSO, while a relocation-free PIC bootstrap can avoid depending on the
target's dynamic-loader internals.

## Binary rewriting and trampolines

An offline rewriter or live detour changes a target call site to branch to a
small architecture-specific trampoline. The trampoline preserves the hook's
register, stack, flags, TLS, signal, and `errno` contract, loads the
process-local context, then calls an `extern "C"` pod entry.

Correct patching is architecture and binary specific. It must decode complete
instructions, handle branch range, relocate overwritten instructions, honor
W^X, flush the instruction cache where required, and account for hardening such
as CET/IBT or AArch64 BTI/PAC. Live patching requires stop-the-world or another
protocol that prevents a thread from executing partially installed bytes.

Dynamic binary instrumentation can supply equivalent hooks without permanent
text changes, at higher steady-state overhead.

## Trust and lifecycle rules

- Authenticate code before execution. A layout fingerprint detects accidental
  incompatibility; it is not an authenticity check.
- Keep executable and mutable mappings separate. Never require RWX pages.
- Validate every method offset, signature, length, alignment, generation, and
  required address before publishing the context.
- Initialize shared state exactly once and publish readiness with release/acquire
  ordering.
- Define unload and reclamation before allowing detach. Existing trampolines
  must never target unmapped code or state.
- Treat writable participants as mutually trusted. A compromised participant
  can corrupt shared bytes regardless of the injection mechanism.

## Recommended progression

Use `LD_PRELOAD` first because it exercises an unaware program with ordinary
ELF machinery. Next, reuse the same bootstrap context and C ABI in a ptrace
loader that maps, hooks, and detaches. Only then add production binary rewriting;
its instruction-relocation and hardening surface is substantially larger than
the shared-memory mechanism itself.

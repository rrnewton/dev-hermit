# Loading pods into an existing process

`shmem-pod` keeps injection separate from the shared-memory object ABI. An
injector first places file descriptors in a process, then calls a small adapter
which authenticates and maps the pod. Steady-state hooks call the validated
pod entry points directly; ptrace, a broker, and serialization are not on that
path.

The common contract is [`BootstrapContext`]. It is a 160-byte `#[repr(C)]`,
pointer-free value with a stable little-endian encoding. It identifies:

- the connector and failure policy;
- artifact, RX-code, and RW-state descriptors in the receiving process;
- exact lengths, state generation, and optional fixed virtual addresses;
- the generated API fingerprint and expected artifact SHA-256; and
- a per-launch nonce which detects accidental context reuse.

Descriptor numbers are process-local. The C entry point copies the context and
duplicates every descriptor before returning; it never retains the caller's
pointer.

## Constructing a context

Construction is fallible so it cannot return a knowingly incoherent value.
Fixed-address and control-socket bits are set only together with their fields:

```rust
use shmem_pod::injection::{
    BootstrapContext, BootstrapFlags, ConnectorKind,
};

# fn example() -> Result<(), shmem_pod::injection::BootstrapError> {
let context = BootstrapContext::new(
    ConnectorKind::Preload,
    BootstrapFlags::REQUIRED.union(BootstrapFlags::INHERIT_ACROSS_EXEC),
    10,                         // complete artifact FD
    11,                         // immutable code FD
    12,                         // mutable shared-state FD
    12_208,                     // complete artifact bytes
    64 * 1024,                  // page-aligned state file bytes
    7,                          // nonzero state generation
    0x1234,                     // generated API fingerprint
    [0x5a; 32],                 // expected artifact SHA-256
    [0xa5; 16],                 // per-launch nonce
)?;
context.validate()?;
# Ok(())
# }
```

Use `with_fixed_code_address`, `with_fixed_state_address`, or
`with_fixed_addresses` when the pod relies on a prearranged virtual-address
window. Those builders validate page alignment and reject aliased code/state
addresses.

[`BootstrapContext::validate`] checks representation and cross-field
coherence. It cannot inspect the OS objects behind the integers. Every adapter
must additionally check file type, identity, length, seals, digest, image
header, API fingerprint, generation, mapping addresses, and final page
permissions before publishing a callable context.

## Trust boundary

A sealed descriptor is immutable; it is not authenticated merely because it is
sealed. SHA-256 checks that the artifact matches the digest in the context; it
does not prove who selected both values. The launch nonce prevents accidental
reuse, not malicious substitution. If an attacker can replace the context,
digest, and all descriptors together, the context cannot detect that.

Authorization therefore comes from an external channel:

- a trusted parent creates the descriptors and controls inheritance before
  executing an unaware child;
- a cooperative host supplies an already-authenticated descriptor table; or
- an adapter authenticates a Unix peer and receives descriptors with
  `SCM_RIGHTS` using a defined message protocol.

The supplied preload demo uses the first model. It stores the encoded context
in an immutable, no-exec memfd and exposes only that descriptor number through
[`BOOTSTRAP_FD_ENV`]. The environment is a locator, not an authorization
secret. This is safe against accidental mutation under the demo's trusted
launcher/participant threat model; it is not a sandbox boundary against a
hostile guest which can rewrite its own descriptors before first use.

[`BootstrapFlags::SCM_RIGHTS_TRANSPORT`] is only a provenance assertion. An
adapter sets it with `with_scm_rights_provenance` *after* authenticating the
peer, framing one message, and associating the received descriptors with their
roles. Core validation checks that a distinct control descriptor is present,
but cannot prove any `recvmsg` occurred. This release does not provide an
SCM_RIGHTS receiver, and the demo adapter rejects contexts carrying that flag.

## `LD_PRELOAD` connector

`demos/preload` injects into a dynamically linked Rust program which knows only
libc's `getuid` ABI. The host:

1. verifies the compiler-produced artifact digest;
2. creates an immutable code memfd and a separate mutable state memfd;
3. creates and seals the encoded bootstrap-context memfd;
4. deliberately clears `FD_CLOEXEC` only on the descriptors descendants need;
5. starts the guest with the shim in `LD_PRELOAD`; and
6. verifies exact attachment and intercepted-call totals in shared state.

The shim constructor only registers `pthread_atfork`; it does not allocate or
map under the dynamic-loader lock. The first ordinary `getuid` call performs
lazy initialization. It validates the sealed context file, duplicates all
descriptors with `FD_CLOEXEC`, authenticates the artifact, maps code RX and
state shared-RW behind guard pages, checks the generated API and generation,
and verifies `/proc` permissions before publishing an immutable process-local
context.

The interposer issues the real `getuid` as a raw syscall, saves/restores
`errno`, and uses a thread-local recursion guard. A recursive call made by the
adapter itself bypasses instrumentation; this prevents initialization deadlock
and is intentionally not counted.

### Fork behavior

The at-fork prepare handler serializes concurrent forks, disables new hook
entries, and waits for admitted calls to drain. Parent and child handlers reset
only an exactly disabled, quiescent gate. The child keeps a fully published
mapping, clears its per-process attachment marker, and records itself on its
next hook call. An impossible copied `INIT_BUSY` value is converted to
`INIT_FAILED`, never retried against potentially unwritten context bytes.

This protocol applies to libc `fork` paths which run `pthread_atfork` handlers.
It does not cover raw `fork` syscalls, `vfork`, or a fork initiated from inside
an admitted hook (which would wait for its own token). Integrations which need
those operations must disable the hook around them or provide a connector-
specific protocol.

### Signals and initialization safe points

The public context and admission gate are allocation-free, but this demo shim
uses Rust `std`, the dynamic loader, allocation, TLS, `/proc`, and runtime
mapping code during first initialization. It is **not async-signal-safe** and
must not initialize from a signal handler, an allocator callback, or while the
thread holds loader/allocator locks. A signal which re-enters `getuid` on the
same thread is bypassed by the recursion guard, but that does not make a first
call from an unrelated signal safe.

Production preload integrations should invoke an explicit early safe-point
initializer before enabling hooks, or replace the lazy path with an audited
allocation-free bootstrap.

## Ptrace bootstrap and detach

`scripts/run-ptrace-demo.sh` exercises an executable Linux x86-64 adapter. Its
C target has no pod, Rust, shim, or method-table dependency. Two generic
inherited fixture pipes merely announce a deterministic post-exec safe point
and hold the single thread there.

The injector then:

1. uses parent-to-child `PTRACE_SEIZE` and `PTRACE_INTERRUPT`;
2. saves registers and the patched instruction/stack words;
3. performs a temporary remote `mmap` syscall;
4. writes the shim pathname and the same encoded `BootstrapContext`;
5. remotely calls `dlopen(..., RTLD_NODELETE)` and
   `shmem_pod_bootstrap_v1`;
6. restores bytes, registers, and stack on every successful operation;
7. removes scratch memory and calls `PTRACE_DETACH`; and
8. releases the fixture only after detach.

The bootstrap call records one shared pod update. The host waits for the target
to exit and checks exactly one attachment and one update, so successful output
proves the target executed independently after the injector detached.

This is bounded evidence, not a general-purpose injector. The demo supports a
single stopped thread, matching glibc/module layouts, a dynamically linked
target, and descriptors inherited from the parent before exec. A production
injector for an arbitrary already-running process must also establish a secure
descriptor-transfer path, seize every thread while handling clone/exec races,
preserve pending signals and restartable syscalls, find a loader-safe point,
handle namespaces/LSMs, and unwind partial remote operations. The script prints
kernel, seccomp, and ptrace-policy diagnostics if host policy denies the proof.

The direct C entry distinguishes malformed context, invalid transport,
incompatible image, disabled gate, same-thread recursion, and other
initialization failures with [`BootstrapStatus`]. A `REQUIRED` direct caller is
responsible for rejecting or terminating its target when the status is not
`Ok`; the demo injector kills the fixture on every failure.

## Binary patches and trampolines

[`ConnectorKind::Trampoline`] uses the same context and C entry, but this crate
does not ship an instruction rewriter. A live or offline patcher must decode
complete instructions, relocate overwritten instructions, honor branch range,
preserve the platform ABI/TLS/`errno`/signal contract, enforce W^X, flush the
instruction cache where required, and handle CET/IBT or AArch64 BTI/PAC. Live
patching also requires stop-the-world or an architecture-specific atomic patch
protocol.

## Failure, detach, and unload

`REQUIRED` preload failures print once with raw `write` and terminate that
process with `_exit(125)`. The demo host places the process tree in one group
and kills remaining descendants when the root reports failure. Non-required
contexts leave the original libc call working but stop attempting initialization
after the first failure.

The adapter init claim publishes `READY` only after the complete context write;
its drop guard publishes `FAILED` on every error or unwind, so no panic can
strand `INIT_BUSY`. Release builds use `panic=abort`, which terminates rather
than unwinding across the C ABI.

Ptrace maps survive detach. The demo uses `RTLD_NODELETE` so its entry points
cannot be unmapped while the detached target may call them. The finalizer stops
new admissions and drains existing calls, but it deliberately does not reclaim
the mapping context. A real unloader must first remove every external hook or
trampoline, disable admission, wait for zero active calls, and only then unmap
code and state. Calling `dlclose` from inside an admitted hook is unsupported
because drain would wait for the caller's own token.

Run the two unaware-program proofs from the crate root:

```console
./scripts/run-preload-demo.sh
./scripts/run-ptrace-demo.sh
./scripts/test-connector-failures.sh
```

The failure script substitutes a structurally valid but incorrect artifact
digest. It requires the preload path to terminate before the guest spawns its
tree and the ptrace entry to return `BootstrapStatus::IncompatibleImage` (`-3`)
before the injector kills the stopped target.

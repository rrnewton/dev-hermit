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

The shim has no ELF constructor and performs no adapter work under the dynamic-
loader lock. The first ordinary `getuid` call registers `pthread_atfork` and
performs lazy initialization at that admitted safe point. Registration has its
own PID-tagged publication state, and `READY` retains that owner. Every exported
hook checks a separate process PID epoch before call-gate admission. This also
covers a fork which snapshots its callback list before another thread installs
the shim handler: a child which inherits foreign `READY` serializes one recovery,
forcibly discards copied process-local gate counts, resets attachment/failure
markers, and rebinds the installed handler to the child without registering it
again. Foreign `EMPTY` is known not to be installed and may register normally
after recovery. Foreign `BUSY` is ambiguous about installation and fails closed
immediately; it is never waited on, reset, or re-registered. Initialization then
validates and pre-reads each descriptor, authenticates the artifact and code
bytes, maps code RX and state shared-RW behind guard pages, checks the generated
API and state identity envelope, and verifies `/proc` permissions before
publishing an immutable process-local context.

The interposer acquires its thread-local recursion guard before the real
`getuid` syscall or admission gate, closing the pre-guard signal-reentry window.
It issues the real call as a raw syscall and saves/restores `errno`. A recursive
call made by the adapter itself bypasses instrumentation; this prevents
initialization deadlock and is intentionally not counted.

### Fork behavior

The at-fork prepare handler serializes concurrent forks, disables new hook
entries, and waits for admitted calls to drain. Parent and child handlers reset
only an exactly disabled, quiescent gate. The child keeps a fully published
mapping, clears its per-process attachment marker, and records itself on its
next hook call. Running the child callback proves handler installation, so it
may promote an installed `BUSY` registration directly to child-owned `READY`.
An impossible copied `INIT_BUSY` context value is converted to `INIT_FAILED`,
never retried against potentially unwritten context bytes.

The skipped-callback recovery uses
[`AdapterCallGate::force_reset_in_fork_child`] under its unsafe contract: the
PID epoch serializes exactly one recovery before child admission, the surviving
fork thread holds no adapter token, and the post-fork address space is private.
Forking from inside an admitted hook violates that contract and remains
unsupported.

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

### Direct status taxonomy

The direct C ABI uses stable numeric classes, also declared in
`demos/connector/shmem_pod_bootstrap.h`:

- `InvalidTransport` (`-2`) means descriptor type, access mode, seal, length,
  duplication, or `pread` validation failed. Artifact, code, and state
  descriptors are checked independently before mapping. Shared mutable state
  specifically rejects both `F_SEAL_WRITE` and `F_SEAL_FUTURE_WRITE`, even when
  its descriptor is `O_RDWR`.
- `IncompatibleImage` (`-3`) means bytes were readable but their authenticated
  digest/header/code hash/API fingerprint or state identity/generation did not
  match.
- `InitializationFailed` (`-6`) means trusted inputs passed identity checks but
  runtime mapping, binding, permission, or method initialization failed.

No runtime error string is parsed to choose a class. The adapter completes all
descriptor transport checks and every identity check available from the
authenticated header before mapping code. The pod-exported opaque layout is
checked immediately after RX mapping, and a post-attachment generation recheck
remains an identity error (`-3`) even though mapping already occurred. Mapping,
binding, permission, and method-operation failures are runtime errors (`-6`).

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
Any timeout, EOF, or protocol error before the fixture announces readiness
kills the complete target process group and reaps the direct child before the
host returns the error.

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
strand `INIT_BUSY`. Concurrent first callers wait on a private futex and are
woken on either terminal publication. There is no elapsed-time or observation-
count failure for a live initializer. The per-process attachment claim has the
same unwind rule: it resets `ATTACH_BUSY` and wakes waiters unless the PID was
published successfully. Release builds use `panic=abort`, which terminates
rather than unwinding across the C ABI.

If an initializer thread is externally stopped or destroyed without unwinding,
waiters remain blocked: the adapter never times out and steals unpublished Rust
state. That is an explicit fail-stop boundary. A required deployment must have
its supervisor terminate/restart the process rather than treating elapsed time
as ownership proof.

Ptrace maps survive detach. More importantly, the adapter DSO itself is linked
with ELF `DF_1_NODELETE`; safety does not depend on every injector remembering
an `RTLD_NODELETE` flag. `dlclose` may drop a handle but cannot unmap its text,
TLS, or process-local context before process exit. This release intentionally
does not offer in-process adapter unloading or reclamation. Deployments needing
replaceable adapters must first provide a separate external-entry grace period
and hook-removal protocol rather than weakening `NODELETE`.

Run the two unaware-program proofs from the crate root:

```console
./scripts/run-preload-demo.sh
./scripts/run-ptrace-demo.sh
./scripts/test-connector-failures.sh
./scripts/test-preload-unload.sh
```

The failure script directly probes all three status boundaries: unsealed
artifact, short code, read-only state, and real READY `O_RDWR` state files with
`F_SEAL_WRITE` or `F_SEAL_FUTURE_WRITE` return `-2`; digest, code-byte, API-
fingerprint, and generation mismatches return `-3`; and a trusted fixed-code
mapping collision returns `-6`. The unload script checks `DT_FLAGS_1` with
`readelf`, calls a saved adapter entry after `dlclose`, and confirms the object
remains resident with `RTLD_NOLOAD`.

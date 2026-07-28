# Injecting a pod with `LD_PRELOAD`

This demo injects an executable shared-memory pod into a dynamically linked
program that has no pod dependency and no pod-specific source code. A small
preload shim interposes `getuid`, attaches authenticated code and state memfds,
and records calls by invoking a pod method in the guest process.

The host performs all trusted setup:

1. Authenticate the pod artifact by a trusted SHA-256 digest.
2. Create a sealed code memfd and a separate mutable state memfd, then
   initialize the shared state.
3. Encode the common `BootstrapContext` in a separate immutable, no-exec
   memfd.
4. Duplicate only the inherited descriptors without `FD_CLOEXEC`.
5. Spawn the guest with `LD_PRELOAD` and a scoped bootstrap environment.

Each injected process lazily consumes the inherited descriptors on its first
`getuid` call. It independently authenticates the artifact, verifies the memfd
seals, maps code `r-xs` and state `rw-s` behind guard pages, and checks those
permissions through the runtime. Descendant execs inherit the descriptors and
repeat that attachment. The real `getuid` is issued as a raw syscall, and the
shim restores errno before returning.

The guest makes one preflight call before it can create children, then
recursively creates a process tree and runs a fixed number of calls from several
threads in every process. The host checks two exact totals in the shared pod
table: one attachment per process and one update per controlled libc call. This
demonstrates concurrent method dispatch from otherwise unaware programs without
a broker or serialization step.

From the crate root (`jq` is required):

```console
./scripts/run-preload-demo.sh
```

`./scripts/test-connector-failures.sh` also verifies that this path fails closed
when the sealed context carries a deliberately incorrect artifact digest.

The workload is configurable:

```console
POD_DEPTH=3 POD_FANOUT=2 POD_THREADS=4 POD_CALLS=1000 \
  ./scripts/run-preload-demo.sh
```

## Scope and limitations

`LD_PRELOAD` applies only to dynamically linked, non-secure executions that
resolve the selected symbol through the dynamic linker. It does not intercept
static binaries, direct syscalls, or set-user-ID/set-group-ID programs where
the loader strips preload configuration. Programs can also bypass this hook
with symbol binding choices such as deep binding.

The environment variable is only a locator for the sealed context FD. Seals
prevent mutation and the SHA-256 verifies artifact identity, but neither proves
who selected the complete context and descriptor set. The demo trusts its
launcher and all writable participants. A production launcher must establish
provenance through controlled inheritance, an authenticated Unix socket, or a
prearranged descriptor table. The shim fails the demo process with exit status
125 when required setup or a method call fails.

Lazy initialization here uses Rust `std`, allocation, TLS, and filesystem I/O.
The thread-local guard prevents recursive entry through this hook, but it does
not make initialization safe while the dynamic-loader or allocator lock is
held or from a signal handler. At-fork handlers serialize forks, disable and
drain hook calls, then reset the exactly quiescent parent/child gates. Raw
`fork` syscalls, `vfork`, and fork from inside this hook remain unsupported. A
production shim needs an allocation-free early bootstrap or an explicit
safe-point initializer before enabling its hooks.

The finalizer disables admission and drains existing calls, but does not drop
the mapped context. External hooks must be removed before `dlclose`; ptrace
injection pins the DSO with `RTLD_NODELETE`.

The demo launches the guest tree in its own process group. If the required shim
terminates any process and the root reports failure, the host kills remaining
members before dropping its state mapping.

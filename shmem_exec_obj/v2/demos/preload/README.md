# Injecting a pod with `LD_PRELOAD`

This demo injects an executable shared-memory pod into a dynamically linked
program that has no pod dependency and no pod-specific source code. A small
preload shim interposes `getuid`, attaches authenticated code and state memfds,
and records calls by invoking a pod method in the guest process.

The host performs all trusted setup:

1. Authenticate the pod artifact by a trusted SHA-256 digest.
2. Create a sealed code memfd and a separate mutable state memfd, then
   initialize the shared state.
3. Duplicate both descriptors without `FD_CLOEXEC`.
4. Spawn the guest with `LD_PRELOAD` and a scoped bootstrap environment.

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

The environment variables and inherited descriptor numbers are a demo
bootstrap protocol, not an authorization boundary. A production launcher
would pass descriptors over an authenticated Unix socket or a prearranged
descriptor table, minimize inherited environment, and define explicit policy
for attach failures. The shim fails the demo process with exit status 125 when
required setup or a method call fails.

Lazy initialization here uses Rust `std`, allocation, TLS, and filesystem I/O.
The thread-local guard prevents recursive entry through this hook, but it does
not make initialization safe while the dynamic-loader or allocator lock is
held, from a signal handler, or in the post-fork window of a multithreaded
process. A production shim needs an allocation-free early bootstrap or an
explicit safe-point initializer before enabling its hooks.

The demo launches the guest tree in its own process group. If the required shim
terminates any process and the root reports failure, the host kills remaining
members before dropping its state mapping.

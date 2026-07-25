# Shared Memory Executable Object Proof of Concept

## Verdict

The concept is feasible on Linux x86-64 for a deliberately restricted, trusted
object:

- Code is freestanding, relocation-free machine code with a versioned C ABI.
- State has a fixed, pointer-free `#[repr(C)]` layout.
- Every process passes its own state mapping address as a raw pointer.
- Shared mutations use process-visible atomics or locks built from atomics.
- The processes are cooperative. This is not an isolation boundary.

It is not feasible to copy an arbitrary Rust object or ordinary Rust library
into shared memory and invoke its methods safely. `PIC`, `no_std`, and
`panic=abort` are individually insufficient. The compiler in this experiment
enforces relocation and layout rules over reviewed source; it is not a machine
code verifier or sandbox. Executing the resulting bytes is an explicit unsafe
trust boundary.

## Run It

From this directory:

```bash
./scripts/run-poc.sh
```

The defaults create a recursive 13-process exec tree, start two worker threads
per process, and run all three synchronization modes. Each process loads the
same pod instance through `LD_PRELOAD`, but maps its code and state at distinct
PID-derived virtual addresses.

Useful overrides:

```bash
POD_DEPTH=2 \
POD_FANOUT=3 \
POD_THREADS=4 \
POD_ITERATIONS=10000 \
./scripts/run-poc.sh
```

Compile the pod methods with the nightly toolchain used by Reverie and Hermit:

```bash
POD_RUSTC="$(rustup which --toolchain nightly rustc)" ./scripts/run-poc.sh
```

Successful output has one relocation-gate result followed by one exact-total
result for each synchronization mode:

```text
PASS compiler rejected a method containing an external relocation
PASS relocation gate demonstrated it is not a code-safety verifier
PASS mode=coarse ... code_perms=r-xs state_perms=rw-s ...
PASS mode=fine   ... code_perms=r-xs state_perms=rw-s ...
PASS mode=atomic ... code_perms=r-xs state_perms=rw-s ...
```

Every host `PASS` also means:

- an out-of-range method call returned the defined error;
- a forked child faulted when it attempted to write the RX mapping;
- every counter exactly matched the computed process/thread/call total;
- connection records contained unique PIDs, code VAs, and state VAs;
- no call returned a failure status.

## Components

| Crate | Purpose |
| --- | --- |
| `pod-api` | Versioned image header and fixed shared-state ABI |
| `pod-code` | Four freestanding Rust entry points compiled into raw code |
| `pod-compiler` | Invokes `rustc`, audits relocations, and emits the image |
| `pod-loader` | Creates instances, validates headers, maps RX/RW regions, calls entries |
| `pod-preload` | Interposes libc calls and phones home through a pod method |
| `pod-guest` | Recursively execs a process tree and creates concurrent worker threads |
| `pod-host` | Launches a case and validates mappings, records, and exact totals |

Generated objects, images, instances, manifests, and traces stay under
`target/` and are not versioned.

## Runtime Shape

The compiler emits a small image with a 128-byte header followed by four
independently audited function bodies. The host creates one file-backed pod
instance:

```text
file offset 0x0000: image header + method bytes + page padding
                     mapped r-x/shared by every process
file offset 0x1000: PodState + page padding
                     mapped rw-/shared by every process
```

An anonymous `MAP_SHARED` region would survive `fork`, but it cannot be found
again after `exec`. A named instance is used so every recursively exec'd guest
must reopen and remap it. An inherited non-`CLOEXEC` memfd would be another
valid bootstrap mechanism.

The preload path is:

```text
guest getuid/geteuid/getgid/getegid call
  -> LD_PRELOAD wrapper
  -> real Linux syscall, preserving its result and errno
  -> selected raw pod function pointer
  -> explicit *mut PodState argument
  -> shared counter update
```

The pod call itself performs no syscall, allocation, serialization, socket
operation, or pointer swizzling. The credential syscall remains because the
hook preserves the original libc behavior.

## Compiler Contract

`pod-compiler` invokes `rustc` with an explicit target baseline and these
important properties:

```text
-Copt-level=3
-Cpanic=abort
-Crelocation-model=pic
-Ccode-model=small
-Ccodegen-units=1
-Coverflow-checks=no
-Cdebug-assertions=no
-Cforce-unwind-tables=no
-Ctarget-cpu=x86-64
-Cembed-bitcode=no
```

The flags do not establish safety by themselves. The compiler parses the ELF
relocatable object with the Rust `object` crate. For each required `pod_*`
symbol it requires:

1. a nonempty global text symbol;
2. a bounded function that occupies its complete dedicated section;
3. no relocation anywhere inside that range;
4. a relocatable x86-64 ELF object;
5. a separate, nonoverlapping image entry and supported ABI header.

Only the accepted function bytes are copied. The intermediate object contains
other sections, including constructor helpers and `.eh_frame`, with
relocations. They are deliberately not part of the image.

This experiment uses a stricter rule than technically necessary. An internal
call or RIP-relative constant could be supported by linking all needed text
and read-only data into one audited RX blob. Rejecting every entry relocation
keeps this proof small and makes each copied method independently movable.

These checks establish relocatability, not memory safety or semantic
confinement. Relocation-free code can still contain inline assembly, syscalls,
an absolute access, a wrong C signature, or an out-of-bounds access through the
state pointer. `pod-code` has compile-time assertions against the ABI function
pointer types, and its disassembly is audited, but `pod-loader::register` and
`pod-loader::add` remain unsafe operations. Arbitrary pod source is out of
scope.

The accepted pod source must not use:

- heap allocation, `std`, TLS, or process-local globals;
- Rust references as its external ABI, especially `&mut`;
- trait objects, vtables, heap pointers, or stored function pointers;
- libc, PLT/GOT imports, compiler-builtins, or unwinding;
- implicit panic paths, checked indexing, formatting, or dynamic copies;
- address-bearing read-only or mutable statics.

`fixtures/relocating-pod.rs` deliberately calls an undefined external symbol.
The harness requires the compiler to reject its relocation.
`fixtures/unsafe-absolute-pod.rs` is the complementary counterexample: it has
no relocation but writes to absolute address 1. The compiler can accept it,
which demonstrates why accepted artifacts must still come from trusted,
reviewed source. The fixture must never be executed.

## Shared State And Synchronization

`PodState` is 33,536 bytes, aligned to 64 bytes, and initialized in the shared
mapping before any guest attaches. It contains no allocator-owned pointers.
The active fields are:

```text
0x000 StateHeader
0x040 connection/start control atomics
0x080 one cache-padded coarse spinlock
0x0c0 four coarse-lock-protected u64 counters
0x100 four cache-padded fine lock/counter pairs
0x200 four cache-padded AtomicU64 counters
0x300 512 cache-padded connection records
```

The modes exercise different machine-level behavior:

| Mode | Operation | Property | Limitation |
| --- | --- | --- | --- |
| `coarse` | One CAS spinlock around the whole table | Every counter method serializes | Highest contention; owner death wedges it |
| `fine` | One CAS spinlock per counter | Different counters can progress independently | Owner death wedges one shard |
| `atomic` | `AtomicU64::fetch_add(Relaxed)` | No software lock or owner state | Contention still serializes the cache line |

The disassembly contains `lock cmpxchg`, `pause`, protected loads/stores, and
`lock add`. Acquire/release ordering protects the non-atomic locked counters.
Relaxed ordering is enough for independent numeric totals. Connection records
are published with release/acquire ordering.

The spinlocks are safe for normal concurrent completion, which the stress
tests exercise. They are not crash-robust or fair. Production choices include
a process-shared robust pthread mutex managed by ordinary DSO code, an audited
futex/owner protocol, or limiting the shared fast path to lock-free operations.

## W^X And Trust Boundary

The loader never creates an RWX mapping. It populates the instance with
`pwrite`, maps the code page RX, and maps only the state offset RW. It opens a
read-only file descriptor for the executable mapping. `/proc/self/maps`, an
intentional write fault, and `strace` validate the VMA permissions.

This is a mapping-permission proof, not code integrity. The current PoC keeps
code and state in one regular file, so a same-UID process that reopens the file
can overwrite code bytes with `pwrite`. A production design should place code
in a separate memfd, populate it, remove the writable alias, apply write/grow/
shrink/seal seals, and map it RX. State must remain in a separate unsealed
shared object.

`MAP_FIXED_NOREPLACE` requires Linux 4.17 or newer for the intended semantics.
The loader verifies that the returned address exactly equals the requested
address and unmaps/rejects any mismatch.

The RW state is directly writable by every guest. A buggy or hostile native
guest can bypass every method and corrupt locks, counters, or headers. This
model is suitable only for mutually trusting components unless the state is
kept outside the guest's address space.

## LD_PRELOAD Scope

The preload demonstration covers ordinary dynamically linked Linux programs.
It does not intercept:

- static binaries;
- secure-execution (`AT_SECURE`, setuid/setgid) programs that ignore preload;
- direct syscalls or hidden/deep-bound libc calls;
- code that unloads the preload DSO while retaining entry pointers.

The tested process topology is fork/spawn followed by exec, so every process
loads a fresh DSO and independently attaches. Fork-without-exec after preload
initialization is intentionally unsupported: the child detects the inherited
parent PID and refuses pod access rather than reusing the parent's connection.
A production implementation needs PID-aware reinitialization plus carefully
designed `pthread_atfork` handling.

The coarse and fine paths are intended for ordinary call context. They are not
async-signal-safe: a signal that interrupts a thread holding a pod spinlock and
reenters the same method can deadlock. The lock-free counter path is the only
reasonable starting point for signal-context instrumentation.

## Ecosystem Evaluation

The proposed crates solve adjacent problems, not the central code-artifact
constraint:

- `mmap-rs`: a reasonable mapping wrapper. This Linux-only PoC uses `libc`
  directly so `MAP_FIXED_NOREPLACE`, exact offsets, and permissions remain
  visible in the audited code.
- `iceoryx2-bb-container`: potentially useful for a later fixed-capacity data
  model. Four counters and fixed connection records do not justify its larger
  dependency and ABI surface here.
- `mmap-sync`: relevant to read-mostly snapshot/RCU state, not required for
  contended increment semantics.
- `rkyv` and `zerocopy`: useful representation tools, but they do not make
  atomics, mutexes, pointers, or executable bytes process-safe.
- `object`: used by the pod compiler because structured ELF parsing is part of
  the relocation gate.

## Reverie And Hermit Fit

The current Reverie API has a useful integration boundary, but its global
state is not itself a pod:

- [`GlobalTool`](../reverie/reverie/src/tool.rs) accepts owned/Serde request
  and response types and may execute asynchronous, blocking global logic.
- [`Backend`](../reverie/reverie/src/backend.rs) currently owns and eventually
  returns the concrete `GlobalState` value.
- [`reverie-liteinst`](../reverie/reverie-liteinst/src/pun.rs) already uses
  separate RW and RX aliases for generated code, but its trampoline embeds a
  process-local callback address and its documented model does not cover exec.
- Detcore's [`GlobalState`](../hermit/detcore/src/tool_global.rs) contains
  `Arc`, ordinary mutexes, heap maps/sets, Tokio tasks, configuration objects,
  and process-local allocation.
- Detcore's [`Scheduler`](../hermit/detcore/src/scheduler.rs) contains dynamic
  maps, vectors, futures/wakers, random state, and intentionally blocking
  scheduling operations.

The practical integration is an additive shared-memory fast path behind or
beside `GlobalRPC`, not a replacement for arbitrary `GlobalTool` methods.
Good first candidates are vector-clock words, event counts, immutable config
scalars, and other fixed-size atomic state. Scheduler grants, dynamic resource
maps, file I/O, cleanup, futures, and authority remain in the controller.

See [RESULTS.md](RESULTS.md) for the exact host, toolchains, commands, measured
results, and remaining validation gaps.

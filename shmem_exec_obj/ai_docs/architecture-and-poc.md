# Shared Memory Executable Object Proof of Concept

> Historical combined design record. The implementations now live in separate
> `v1/` and `v2/` workspaces; use the repository-root README and each
> iteration's script for current commands.

## Verdict

The concept is feasible on Linux x86-64 for a deliberately restricted, trusted
object. The experiment now proves two useful tiers:

- V1 copies independently relocation-free Rust functions behind a versioned C
  ABI and passes each process's state mapping address explicitly.
- V2 links a complete immutable RX closure, permits audited in-blob PC-relative
  calls, and rejects absolute, undefined, out-of-range-addend, and non-RX
  dependencies.
- The `no_std` `shmem-pod` SDK supports compiler-selected `repr(Rust)` layouts
  when code and state authenticate the same exact structural fingerprint.
- `PodValue` state is pointer-free and may move between process VAs;
  `FixedAddressPodValue` permits audited absolute-address wrappers when every
  process reserves the same VA.
- Shared mutation uses process-visible atomics, process spin locks, or a
  hierarchical lock-free SNZI. A Talc allocator can own an exact shared page
  range through stable `allocator-api2` handles.
- The processes are cooperative. This is not an isolation boundary.

It is not feasible to copy an arbitrary Rust object or ordinary Rust library
into shared memory and invoke its methods safely. `PIC`, `no_std`, and
`panic=abort` are individually insufficient. The compiler in this experiment
enforces relocation and layout rules over reviewed source; it is not a machine
code verifier or sandbox. Executing the resulting bytes is an explicit unsafe
trust boundary.

## Run It

From the repository root:

```bash
./v1/scripts/run-poc.sh
./v2/scripts/run-poc.sh
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
./v1/scripts/run-poc.sh
```

Compile the pod methods with the nightly toolchain used by Reverie and Hermit:

```bash
POD_RUSTC="$(rustup which --toolchain nightly rustc)" ./v1/scripts/run-poc.sh
```

Successful output has one relocation-gate result followed by one exact-total
result for each synchronization mode, V2 negative gates, and a V2 process run:

```text
PASS compiler rejected a method containing an external relocation
PASS relocation gate demonstrated it is not a code-safety verifier
PASS mode=coarse ... code_perms=r-xs state_perms=rw-s ...
PASS mode=fine   ... code_perms=r-xs state_perms=rw-s ...
PASS mode=atomic ... code_perms=r-xs state_perms=rw-s ...
PASS V2 compiler rejected outside-addend
PASS V2 compiler rejected absolute
PASS V2 compiler rejected undefined
v2-ok ... snzi_query=false snzi_quiescent=true ...
```

Every V1 host `PASS` also means:

- an out-of-range method call returned the defined error;
- a forked child faulted when it attempted to write the RX mapping;
- every counter exactly matched the computed process/thread/call total;
- connection records contained unique PIDs, code VAs, and state VAs;
- no call returned a failure status.

The publishable SDK examples can also be run independently:

```bash
cargo run -p shmem-pod --example process_locks
cargo run -p shmem-pod --example snzi
cargo run -p shmem-pod --features fixed-allocator --example fixed_allocator
cargo run -p shmem-pod --features fixed-allocator --example fixed_allocator_exec
```

The final example uses a memfd and `MAP_FIXED_NOREPLACE` at a recorded high
address, then independently execs children which validate the bootstrap and
attach to the same allocator pages.

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
| `shmem-pod` (`pod-v2-types`) | Publishable `no_std` traits, layouts, offsets, locks, SNZI, and optional allocator |
| `shmem-pod-macros` (`pod-v2-derive`) | Recursive structural capability derives and compile-fail checks |
| `pod-v2-code` | Freestanding linked methods using offsets, an arena, and SNZI |
| `pod-v2-compiler` | Builds/audits one RX closure and records transitive build provenance |
| `pod-v2-runtime` | Authenticates, seals, maps, validates, and invokes a V2 image |
| `pod-v2-host` | Exec-process stress and exact allocator/SNZI lifecycle validation |

Generated objects, images, instances, manifests, and traces stay under
`target/` and are not versioned.

## V1 Runtime Shape

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

## V1 Compiler Contract

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

## V2 SDK And Compiler

`shmem-pod` is a candidate crates.io SDK with Rust 1.85 MSRV and a `no_std`
default library. Its unsafe marker traits separate two representation tiers:

- `PodValue`: no typed absolute address, destructor, allocator header, or
  process-local resource; checked `Offset<T>` and `OffsetSlice<T>` express
  links into a mapping.
- `FixedAddressPodValue`: the weaker exact-VA tier used by audited wrappers
  such as `FixedRegionAllocator`.
- `PodSync`: ordinary typed shared access remains race-free through atomics or
  synchronization stored in the shared object itself.

The derives accept concrete `repr(Rust)` structs and fingerprint type identity,
size, alignment, field names, compiler-selected offsets, and transitive field
fingerprints. They reject references, pointers, standard owning collections,
ordinary mutexes, destructors, generics, enums, and unions. This relaxes
`#[repr(C)]` only for authenticated exact-build compatibility; it does not
create a stable Rust ABI or audit method semantics.

The optional fixed allocator uses Talc 5.0.4 behind a process-shared raw lock
and stable `allocator-api2`. It claims exactly the caller's pages and rejects
relocated attachment. A tracing allocator was deliberately deferred: safe
cross-process mark/sweep additionally needs shared roots, mutation barriers,
global coordination, and crash recovery. Explicit allocation/deallocation
proves dense page control without pretending those policies are solved.

`Snzi<NODES>` implements the four-way tree algorithm from Ellen, Lev,
Luchangco, and Moir, including `HALF` helping, parent compensation, activation
generations, and a centralized atomic root. Typed arrival tokens are linear and
bound to the exact issuing instance. The executable pod exposes the same
operations through a checked scalar C ABI.

`pod-v2-compiler` compiles the SDK without default features, links the complete
closure into one `.pod` RX section at VMA zero, and admits only signed 32-bit
PC-relative relocations whose effective target remains inside that section. It
rejects writable/other allocated sections, absolute and undefined references,
and section-symbol addends which escape the blob. Its manifest hashes rustc,
rust-lld, linker script, objects, rlib, dep-info, and every transitive source
reported by rustc.

## V1 Shared State And Synchronization

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
  model. The current SDK instead keeps its core structures fixed-size and uses
  checked offsets for relocatable links.
- `mmap-sync`: relevant to read-mostly snapshot/RCU state, not required for
  contended increment semantics.
- `rkyv` and `zerocopy`: useful representation tools, but they do not make
  atomics, mutexes, pointers, or executable bytes process-safe.
- `object`: used by the pod compiler because structured ELF parsing is part of
  the relocation gate.
- `talc`, `lock_api`, and `allocator-api2`: used by the optional fixed-address
  allocator to constrain all allocation metadata and buffers to supplied pages.

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

See [results.md](results.md) for the exact host, toolchains, commands, measured
results, and remaining validation gaps.

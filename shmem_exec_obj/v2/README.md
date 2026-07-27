# shmem-pod

`shmem-pod` provides `no_std` building blocks for Rust objects that live
directly in shared memory. Multiple trusted processes can map the same pages,
validate the stored layout, and call ordinary Rust methods on the shared object
without serializing requests through a server.

The crate focuses on the hard parts of the state representation:

- structural marker traits and derives reject process-local pointers and
  allocator ownership;
- exact compiled-layout fingerprints let an attacher validate the Rust layout
  before forming a typed reference;
- checked relative offsets work when each process maps the state at a different
  virtual address;
- process-shared atomics, spin locks, Linux futex locks, and SNZI cover several
  synchronization patterns; and
- an optional Talc allocator uses an exact caller-supplied range of shared
  pages.

The application remains responsible for creating and transporting the shared
mapping, authenticating any executable image, publishing initialization, and
controlling teardown.

## Add The Crate

```toml
[dependencies]
shmem-pod = "0.1"
```

The default `derive` feature exports the marker-trait derives. Optional
features are:

| Feature | Adds |
| --- | --- |
| `linux-futex` | `ProcessFutexMutex`, which blocks with shared `FUTEX_WAIT`/`FUTEX_WAKE` after a short spin |
| `fixed-allocator` | `FixedRegionAllocator` and the re-exported `allocator-api2` collection types |

The library itself is `no_std`. The executable examples use Linux APIs to
create and share mappings.

## Define Shared State

A relocatable object derives `PodValue`. A concurrently accessible object also
derives `PodSync`:

```rust
# #[cfg(feature = "derive")]
# fn derived_state_example() {
use core::sync::atomic::{AtomicU64, Ordering};
use shmem_pod::sync::ProcessSpinMutex;

#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct GlobalState {
    requests: AtomicU64,
    by_kind: ProcessSpinMutex<[u64; 4]>,
}

let state = GlobalState {
    requests: AtomicU64::new(0),
    by_kind: ProcessSpinMutex::new([0; 4]),
};

state.requests.fetch_add(1, Ordering::Relaxed);
state.by_kind.lock()[2] += 1;
# }
```

This value is on the stack only to introduce the types. In an application, one
process constructs it exactly once at its final address in a writable
`MAP_SHARED` mapping. Attachers form a reference only after the validation
sequence below.

### Why `#[repr(C)]` Is Not Required

The derives fingerprint the compiler-selected size, alignment, field offsets,
field identities, and transitive field fingerprints. `LayoutDescriptor` carries
that fingerprint in a stable 48-byte encoding. An attacher compiled with a
different layout rejects the mapping before interpreting its payload.

This permits ordinary Rust representation, but it is exact compiled-layout
compatibility, not a stable Rust ABI or a build identity. As loader policy, all
participants should use the same authenticated code build, target, compiler,
dependency lock, and feature set. Use a separately versioned wire format when
state must survive arbitrary software upgrades or be consumed by another
language.

The derives are structural checks, not method audits. In particular, an integer
field is treated as data even if unsafe application code later casts it to a
pointer. Derivation also does not make a numeric file descriptor meaningful in
another process.

## Choose An Address Tier

`PodValue` is the preferred tier. It excludes typed absolute pointers,
references, function pointers, vtables, and allocator headers. The same bytes
remain meaningful when mapped at different virtual addresses. Store links as
`Offset<T>` or `OffsetSlice<T>` and resolve them through a process-local
`PodRegion`.

`FixedAddressPodValue` is the weaker tier for an audited type whose contents
are valid only when every process maps the pages at the same numeric addresses.
The derive still rejects raw pointers and standard owning collections because
they have no generally valid shared-memory contract. A pointer-bearing wrapper
requires a manual `unsafe impl` whose constructor and loader jointly establish
the exact-address and ownership rules.

This tier is experimental with respect to Rust's strict-provenance model. The
Linux tests prove that equal numeric addresses work operationally, but Rust does
not yet give a clear general guarantee that a typed pointer value persisted by
one process carries usable provenance in another. Prefer `PodValue` plus integer
offsets for the strongest contract.

`PodSync` is independent of either storage tier. It says ordinary shared
references are safe while other processes may access the same object. Mutable
non-atomic fields therefore need a process-shared synchronization primitive.

| Capability | Mapping address | Stored links | Typical use |
| --- | --- | --- | --- |
| `PodValue` | may differ per process | checked relative offsets | counters, tables, relocatable graphs |
| `FixedAddressPodValue` | identical in every process | audited absolute addresses allowed | shared allocator metadata |
| `PodSync` | either tier | requires race-free shared access | state exposed through shared references |

## Initialize And Attach

Treat typed access as the last step of a loader protocol:

1. Create the state mapping as shared, writable, and non-executable. Map code in
   separate read-execute pages if the application also distributes machine
   code.
2. Give one initializer exclusive ownership. It constructs every atomic, lock,
   descriptor, and payload object directly at its final address.
3. Bind the encoded `LayoutDescriptor` to the authenticated code/build identity
   and mapping geometry. Publish a ready word with `Release` ordering only after
   every preceding write is complete.
4. An attacher initially treats the mapping as untyped bytes. It authenticates
   the build, acquires the ready word, decodes and validates the descriptor,
   checks every extent and alignment, and only then forms a Rust reference.
5. Before teardown, close admission, drain all users and SNZI arrivals, return
   allocator blocks, destroy owning state exactly once, and unmap only after
   every process has detached.

A fork-only child inherits the already initialized mapping, code build, and
virtual addresses. Fork only while guards, arrival tokens, and allocator-backed
owners are quiescent; reconstruct working references from the inherited mapping
base and never unwind duplicated owners. An independently started or exec'd
attacher has no such inherited validation context and must perform the complete
descriptor and artifact handshake before forming references.

`LayoutDescriptor::validate::<T>()` compares the received fingerprint, size,
and alignment with the local `T`. A descriptor is a compatibility check, not a
signature and not proof that the payload bytes contain a valid `T`.

Run the complete release/acquire handshake:

```text
cargo run --example layout_handshake
```

## Use Relative Offsets

An `Offset<T>` stores a byte displacement, not an address. Each process creates
its own `PodRegion` from its local mapping base and resolves the displacement
there:

```rust
use core::mem::size_of;
use shmem_pod::offset::{Offset, PodRegion};

let mut words = [10_u64, 20, 30];
let region = unsafe {
    PodRegion::from_raw_parts(words.as_mut_ptr().cast(), size_of::<[u64; 3]>())?
};
let second = Offset::<u64>::new(size_of::<u64>() as u64).unwrap();
assert_eq!(unsafe { region.get(second)? }, Some(&20));
# Ok::<(), shmem_pod::offset::ResolveError>(())
```

Resolution checks null encodings, target-width conversion, alignment, and the
complete target extent. It remains unsafe because byte-range checks cannot
prove that the target is initialized and valid or that other processes obey
its synchronization protocol. `PodRegion` contains a process-local base and
must not itself be stored in shared memory.

For a field in a default-layout struct, derive the displacement from the same
base used by `PodRegion`: `payload_offset + core::mem::offset_of!(State, field)`.
Use checked arithmetic and integer conversion. If the region begins at the
payload itself, `offset_of!` is sufficient; if it begins at a bootstrap header,
include the validated payload offset.

The `relative_offsets` example maps one file twice at different addresses and
resolves the same stored offsets through both mappings:

```text
cargo run --example relative_offsets
```

## Choose Synchronization

Use the narrowest primitive that expresses the operation:

| Primitive | Best for | Waiting behavior | Important limitation |
| --- | --- | --- | --- |
| `AtomicU64` and other supported atomics | independent counters, flags, indices | lock-free when the target guarantees it | compound invariants need a stronger protocol |
| `ProcessSpinMutex<T>` | very short, low-contention critical sections | spins for the entire wait | burns a CPU while contended |
| `ProcessFutexMutex<T>` with `linux-futex` | longer or contended Linux critical sections | spins briefly, then sleeps in the kernel | Linux-only and not robust to owner death |
| one coarse mutex | simple invariants spanning the whole object | serializes all callers | limits parallelism |
| fine-grained mutexes | independent shards or records | contention remains local | lock ordering must be designed explicitly |
| `Snzi<N>` | scalable "is anything active?" queries | lock-free arrivals/departures; wait-free query | not an exact counter |

Linux futexes can wait across processes. The lock word lives in the shared
mapping and `ProcessFutexMutex` uses shared futex operations, so the kernel keys
waiters by the shared backing object and offset rather than requiring identical
virtual addresses. The private futex flag must not be used for this case.

Both mutex types are non-reentrant, non-fair, non-robust, and not
async-signal-safe. A process that exits while holding either mutex leaves it
locked. Futex sleeping reduces wasted CPU; it does not by itself provide
owner-death recovery.

The `shared_counters` example runs coarse locking, per-shard locking, and atomic
fetch-add counters under the same multi-process workload:

```text
cargo run --example shared_counters
cargo run --features linux-futex --example futex_mutex
```

## Track Presence With SNZI

`Snzi<NODES>` is a four-way scalable nonzero indicator. Arrivals spread across
leaves while `query` reads a centralized root in constant time. Complete tree
sizes begin at 4, 20, 84, and 340 nodes; an 84-node tree has 64 selectable
leaves.

```rust
use shmem_pod::snzi::Snzi;

let active = Snzi::<84>::new();
let token = active.arrive(7)?;
assert!(active.query());
token.depart()?;
assert!(!active.query());
# Ok::<(), shmem_pod::snzi::SnziError>(())
```

An `ArrivalToken` is linear and tied to its issuing instance. Its raw C ABI
encoding is intended for audited foreign-function boundaries, but the raw
integer cannot enforce single consumption or instance identity.

SNZI reports presence, not the number of arrivals. Process death cannot wedge a
lock because there is no lock, but it can leak an unmatched arrival. A false
query is only a point-in-time observation; close admission before using
quiescence to reclaim state.

```text
cargo run --example snzi
```

## Allocate Inside Known Pages

Enable `fixed-allocator` to bind Talc to an exact caller-supplied shared range:

```toml
[dependencies]
shmem-pod = { version = "0.1", features = ["fixed-allocator"] }
```

`FixedRegionAllocator::initialize` claims the region once. Every later process
maps the same physical pages at the same numeric address and calls
`FixedRegionAllocator::attach` with the identical base and length. Allocation
and deallocation are serialized by a process-shared raw lock.

Talc gives the application dense, explicit control over which pages back
allocator-aware `Vec` and `Box` buffers. It is an explicit allocation/free
allocator, not a tracing collector. A mark-sweep layer would additionally need
a shared root format, stop-the-world or concurrent tracing protocol, and crash
recovery before it could safely infer that an allocation is unreachable.

Because Talc persists typed absolute pointers, this allocator inherits the
strict-provenance limitation of the fixed-address tier. Keep it experimental
until that contract is defensible or its persistent metadata uses integer
offsets instead.

Collection headers still contain absolute buffer pointers and drop ownership.
Keep those headers process-local unless the application defines a separate
single-owner, `ManuallyDrop`-style shared lifecycle. Never let parent and child
drop duplicated owning headers after `fork`.

The fork example inherits one address space layout. The exec example uses a
`memfd`, an explicit high address, `MAP_FIXED_NOREPLACE`, a bootstrap header,
and layout validation to demonstrate the full fixed-address requirement:

```text
cargo run --features fixed-allocator --example fixed_allocator_fork
cargo run --features fixed-allocator --example fixed_allocator_exec
```

## Safety And Failure Model

The marker traits establish representation capabilities only. A complete
system must also enforce these conditions:

- Every mapping is the same intended physical object, has sufficient extent and
  alignment, and uses read-write state pages separate from read-execute code
  pages.
- The complete code artifact, ABI, target, feature set, and descriptor are
  authenticated before dispatch. W^X permissions do not authenticate code.
- Exactly one initializer constructs the state, readiness uses release/acquire
  ordering, and typed references exist only while the mapping remains live.
- All participants are trusted with writable state. Any participant can bypass
  methods, corrupt lock words, forge offsets, or issue raw syscalls.
- Atomic widths and cross-process atomic behavior are supported by the target.
  The current SNZI implementation requires 64-bit atomics.
- Owner death is planned for. Spin and futex mutexes can wedge; SNZI can leak an
  arrival; allocator death can strand the allocator lock or initialization
  state.
- `fork` occurs only while Rust owners are quiescent. A fork duplicates live
  guards, SNZI tokens, and collection headers outside Rust's ownership model.
  An unsafe child should immediately `exec` or `_exit` without unwinding them.

This crate ships no injector, binary patcher, descriptor transport, or
fixed-address selector. The
[injection integration guide](https://docs.rs/shmem-pod/latest/shmem_pod/injection/)
describes how preload shims, ptrace bootstraps, and patched trampolines can use
these primitives; those are integration designs, not injector APIs implemented
by this crate.

## Examples

The examples are self-checking and ordered as a tutorial in
[`examples/README.md`](examples/README.md):

```text
cargo run --example layout_handshake
cargo run --example relative_offsets
cargo run --example shared_counters
cargo run --features linux-futex --example futex_mutex
cargo run --example snzi
cargo run --features fixed-allocator --example fixed_allocator_fork
cargo run --features fixed-allocator --example fixed_allocator_exec
```

The mapping examples target Linux. `fixed_allocator_exec` additionally targets
x86-64 because its demonstration address is architecture-specific.

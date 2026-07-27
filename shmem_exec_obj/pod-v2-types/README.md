# shmem-pod

`shmem-pod` provides `no_std` building blocks for Rust state that lives in a
shared mapping and is accessed directly by multiple processes. It is the SDK
extracted from the executable shared-object proof of concept in
`rrnewton/dev-hermit`.

The crate does **not** turn arbitrary Rust values or arbitrary machine code
into safe IPC. It makes the relevant assumptions explicit and testable:

- `PodValue` marks pointer-free state that may be mapped at different virtual
  addresses in each process.
- `FixedAddressPodValue` is the weaker tier for state whose pointers remain
  valid only when every process uses the same exact virtual addresses.
- `PodSync` marks structural layouts whose ordinary typed access supports
  concurrent process access.
- `ProcessSpinMutex` supplies a short-critical-section process-shared lock.
- `Snzi` supplies a scalable lock-free nonzero indicator.
- the optional `fixed-allocator` feature binds a general allocator to an exact
  caller-supplied shared page range.

The derive macros fingerprint the compiler-selected Rust layout, including
size, alignment, field offsets, and transitive field fingerprints. Therefore
state structs do not need `#[repr(C)]` when the authenticated code image and
state descriptor carry and validate the same fingerprint. This is exact-build
compatibility, not a stable Rust ABI.

These are structural storage capabilities. They do not inspect methods or the
semantic meaning of scalar fields. In particular, `PodValue` treats integers as
data; deriving it does not certify code that casts an integer to a pointer, and
`FixedAddressPodValue` does not make a numeric fd valid in another process.
Calling executable methods remains a separate unsafe artifact/API audit.

## Basic State

```rust
# #[cfg(feature = "derive")]
# fn derived_state_example() {
use core::sync::atomic::AtomicU64;
use shmem_pod::sync::ProcessSpinMutex;

#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct GlobalState {
    serialized: ProcessSpinMutex<[u64; 4]>,
    lock_free: [AtomicU64; 4],
}

let state = GlobalState {
    serialized: ProcessSpinMutex::new([0; 4]),
    lock_free: core::array::from_fn(|_| AtomicU64::new(0)),
};
state.serialized.lock()[0] += 1;
# }
```

Construct the object exactly once in exclusively owned shared pages. Publish a
ready state with release ordering only after initialization is complete. Every
attaching process must authenticate the code and descriptor, validate the
layout fingerprint and mapping bounds, and acquire the ready state with acquire
ordering before forming typed references.

## Mapping And Layout Validation

`LayoutDescriptor` has a stable 48-byte encoding for the exact compiled size,
alignment, and structural fingerprint. Decode and compare it before forming a
typed reference. The descriptor is not code authentication; bind it to an
authenticated executable artifact separately. This self-contained Linux
example uses sequential initialization and attachment; a concurrent loader must
publish readiness with release/acquire ordering as described above.

```rust,no_run
use core::{mem, ptr, slice};
use shmem_pod::layout::LayoutDescriptor;
use shmem_pod::sync::ProcessSpinMutex;

# #[cfg(feature = "derive")]
#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct SharedState {
    total: ProcessSpinMutex<u64>,
}

# #[cfg(feature = "derive")]
# fn main() -> Result<(), Box<dyn std::error::Error>> {
let expected = LayoutDescriptor::of::<SharedState>();
let encoded = expected.encode();
let payload_offset = (LayoutDescriptor::ENCODED_LEN + mem::align_of::<SharedState>() - 1)
    & !(mem::align_of::<SharedState>() - 1);
let mapping_len = 4096;
assert!(payload_offset + mem::size_of::<SharedState>() <= mapping_len);

let mapping = unsafe {
    libc::mmap(
        ptr::null_mut(),
        mapping_len,
        libc::PROT_READ | libc::PROT_WRITE,
        libc::MAP_SHARED | libc::MAP_ANONYMOUS,
        -1,
        0,
    )
};
assert_ne!(mapping, libc::MAP_FAILED);
let base = mapping.cast::<u8>();

// The initializer exclusively owns the mapping until both writes complete.
unsafe {
    ptr::copy_nonoverlapping(encoded.as_ptr(), base, encoded.len());
    base.add(payload_offset)
        .cast::<SharedState>()
        .write(SharedState { total: ProcessSpinMutex::new(0) });
}

// An attacher first treats the bootstrap bytes as untyped input.
let wire = unsafe { slice::from_raw_parts(base, LayoutDescriptor::ENCODED_LEN) };
let received = LayoutDescriptor::decode(wire)?;
received.validate::<SharedState>()?;
assert!(payload_offset + received.size() as usize <= mapping_len);
let payload = unsafe { base.add(payload_offset) };
assert_eq!(payload as usize % received.alignment() as usize, 0);

// Only after artifact, lifecycle, descriptor, extent, and alignment checks is
// the initialized typed reference formed.
let state = unsafe { &*payload.cast::<SharedState>() };
*state.total.lock() += 1;
assert_eq!(*state.total.lock(), 1);
unsafe { ptr::drop_in_place(payload.cast::<SharedState>()) };
assert_eq!(unsafe { libc::munmap(mapping, mapping_len) }, 0);
# Ok(()) }
# #[cfg(not(feature = "derive"))]
# fn main() {}
```

## Relative Offsets

Use typed integer offsets when processes may choose different mapping bases.
Resolution checks the complete extent and alignment. Creating a Rust reference
is still unsafe because bounds checks cannot prove that bytes contain a valid,
initialized `T` or that another process follows its synchronization protocol.

```rust
use core::mem::size_of;
use shmem_pod::offset::{Offset, PodRegion};

let mut words = [10_u64, 20, 30];
let region = unsafe {
    PodRegion::from_raw_parts(
        words.as_mut_ptr().cast(),
        core::mem::size_of_val(&words),
    )?
};
let second: Offset<u64> = Offset::new(size_of::<u64>() as u64).unwrap();
assert_eq!(*unsafe { region.get(second)? }.unwrap(), 20);
# Ok::<(), shmem_pod::offset::ResolveError>(())
```

`OffsetSlice<T>` applies the same checks to a stored offset and element count.
Only the integer descriptors belong in shared state; `PodRegion` contains a
process-local base address.

## SNZI

`Snzi<NODES>` is the paper's hierarchical nonzero indicator with four-way
branching, `HALF`-state helping, activation generations, and a centralized
atomic root. Valid complete-tree node counts start at 4, 20, 84, and 340.

```rust
use shmem_pod::snzi::Snzi;

let active = Snzi::<84>::new(); // 64 selectable leaves
let token = active.arrive(7)?;
assert!(active.query());
token.depart()?;
assert!(!active.query());
# Ok::<(), shmem_pod::snzi::SnziError>(())
```

Typed tokens are linear and borrow their issuing instance, preventing safe
cross-instance departure. Their consuming raw C ABI encoding carries only a
leaf and activation generation; raw departure is unsafe because the scalar is
copyable and does not encode instance identity.

## Fixed-Address Allocation

Enable `fixed-allocator` to claim an exact caller-supplied shared page range
with Talc and use it through stable `allocator-api2`. The `fixed_allocator`
module contains a compiled mapping, initialization, allocation, attachment, and
teardown example when that feature is enabled.

The allocator control object must itself live in shared pages. Initialization
is exclusive; later processes call unsafe `attach` with the identical numeric
base and length. Talc stores absolute pointers, so a relocated mapping is
rejected. All participants must authenticate the same complete code build,
compiler target, dependency lock, and feature set.

The module's anonymous example is explicitly fork-only. The
`fixed_allocator_exec` example additionally creates file-backed pages, inherits
their fd across `exec`, remaps them at a recorded high virtual address with
collision-safe `MAP_FIXED_NOREPLACE`, and validates the returned address, stable
bootstrap fields, mapping geometry, readiness, and layout descriptor before
attaching. The module documents the complete loader checklist.

Allocator-aware `Vec` and `Box` buffers can live in the claimed pages, but their
owning headers contain absolute pointers and `Drop` state. Do not place those
headers directly in shared state without a separate `ManuallyDrop`-style,
single-owner lifecycle protocol and process-shared synchronization.

This first allocator is explicit alloc/dealloc rather than tracing garbage
collection. A mark-sweep allocator would also need a shared root format, a way
to stop or coordinate every mutating process, and a crash policy before it could
safely decide that memory is unreachable. Talc proves exact page control and
stable allocator-aware collections with a much smaller contract. A later
tracing layer can be built on the same claimed pages once those lifecycle rules
are specified.

## Storage Tiers

| Capability | Different mapping addresses | Absolute pointers | General allocator |
| --- | --- | --- | --- |
| `PodValue` | yes | no | no; use checked offsets |
| `FixedAddressPodValue` | no | allowed by an audited wrapper | `fixed-allocator` |

Derives reject standard `Vec`, `Box`, references, raw pointers, ordinary
`std::sync::Mutex`, fields with destructors, and fields lacking the selected
capability. Fixed-address support is not a blanket exemption: use an audited
pointer or allocator wrapper whose unsafe constructor establishes the mapping
contract.

## Synchronization Limits

`ProcessSpinMutex` is non-fair and consumes CPU while waiting. It is not
reentrant, async-signal-safe, or robust. A process that dies while holding it
wedges the lock. Keep critical sections short and use atomics or SNZI when the
operation permits it.

SNZI answers whether one or more unmatched arrivals exist; it is not an exact
counter. Process death cannot wedge its lock-free algorithm, but can leak an
arrival. A false query is also not a reclamation barrier because a new arrival
may race immediately after it; close admission before reclaiming state.

`fork` bypasses Rust ownership and duplicates live guards, SNZI tokens, and
allocator-backed collection headers. That can unlock a mutex early, consume one
arrival twice, or double-free a shared allocation. Fork only from a quiescent
state with no such live owners. Otherwise the child must never access or drop
the duplicated owners and must terminate with `exec` or `_exit` rather than
unwinding them; immediately executing a new image is the safest policy.

## Loader Boundary

A native loader remains responsible for W^X mappings, executable-image
authentication, ABI validation, state lifecycle, exact-address reservation for
fixed values, and protection against malformed or hostile guests. A guest with
a writable state mapping can bypass methods and corrupt bytes. This model is
for mutually trusting native processes unless a stronger isolation boundary is
added.

The repository examples are self-checking Linux programs:

```text
cargo run -p shmem-pod --example process_locks
cargo run -p shmem-pod --example snzi
cargo run -p shmem-pod --features fixed-allocator --example fixed_allocator
cargo run -p shmem-pod --features fixed-allocator --example fixed_allocator_exec
```

## Status

This is an experimental API being hardened toward a crates.io release. The
current implementation targets 64-bit atomic platforms and is exercised on
x86-64 Linux. Cross-architecture shared-atomic behavior, owner-death recovery,
state upgrades, and hostile-process isolation are not yet provided.

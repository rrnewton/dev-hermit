# Relocatable Allocation

`RelocAllocator` divides caller-mapped shared pages into a fixed number of
equal-size allocation slots. Its persistent metadata contains integer offsets,
integer geometry, generation counters, a bitmap, and atomics. It never stores a
pointer. Each process supplies its own mapping base when it initializes or
attaches, so one backing object can be mapped at different virtual addresses.

## Memory Layout

Choose `RelocAllocator<SLOTS>` and reserve three disjoint areas in one writable
shared mapping:

1. authenticated bootstrap metadata;
2. one initialized `RelocAllocator<SLOTS>` object; and
3. a 64-byte-aligned arena of exactly `SLOTS * slot_size` bytes.

`slot_size` must be a nonzero multiple of 64. One allocation occupies one slot,
so its size must not exceed `slot_size` and its alignment must not exceed 64.
This deliberately bounded design makes metadata dense and auditable. It is not
a general variable-size heap.

The initializer calls:

```rust,ignore
let region = unsafe {
    allocator.initialize(
        local_mapping_base,
        mapping_len,
        fresh_region_id,
        arena_offset,
        slot_size,
    )?
};
```

An independently started process authenticates the backing object and exact
code build, validates the allocator's layout descriptor, maps the bytes, and
then calls:

```rust,ignore
let region = unsafe {
    allocator.attach(local_mapping_base, mapping_len, expected_region_id)?
};
```

The allocator records the control object's mapping-relative offset and rejects
an attacher which presents a different object or geometry. `RelocRegion` holds
the process-local pointer and must never be persisted.

## Allocation Identity

Each `AllocationDescriptor` binds:

- a nonzero mapping-generation identity;
- a slot index and reuse generation;
- the exact mapping-relative byte offset and extent;
- the required alignment; and
- a collection-specific, transitive Rust layout fingerprint.

Resolution compares every field with live allocator metadata before deriving a
local pointer. Freeing increments the generation. Stale copies, double frees,
wrong-region frees, changed offsets, type confusion, and bitmap/slot
disagreement therefore fail before payload access. Generation wrap poisons the
whole allocator rather than reviving an old descriptor.

## Shared Collections

`SharedBox<T>` allocates and initializes one `PodValue`. `SharedVec<T>` reserves
fixed capacity in one slot and tracks an initialized prefix. Both collection
descriptors contain only integers and `PhantomData`; neither has `Drop`.

```rust,ignore
let mut counter = SharedBox::new(&region, AtomicU64::new(0))?;
counter.get(&region)?.fetch_add(1, Ordering::Relaxed);

let mut values = SharedVec::with_capacity(&region, 32)?;
unsafe { values.push(&mut region, 10)? };
assert_eq!(values.as_slice(&region)?, &[10]);
```

Safe shared resolution requires `T: PodSync`. Mutation is unsafe because a
Rust `&mut` in one process cannot prove that another process has stopped using
the same pages.

## Explicit Destruction

There is intentionally no automatic destructor. Before `destroy`, the host
must close admission, stop or detach every participant, and exclude all
resolved references and raw descriptor copies across the complete process set.
The method is unsafe to make that global obligation explicit:

```rust,ignore
let value = unsafe { counter.destroy(&mut region)? };
unsafe { values.destroy(&mut region)? };
```

Safe `get` and `as_slice` rely on this rule: an unsafe destroy must never race a
borrow returned by safe resolution. After successful destruction, the slot's
generation changes and stale descriptors are rejected.

## Bounded Contention And Crashes

Allocation and destruction use a small process-shared lock with a fixed retry
budget. Contention returns `RelocError::Busy`; callers may yield, back off, or
cancel. The API never changes a timeout into lock ownership and never spins
without a bound.

A Rust unwind during in-place initialization poisons the allocator and releases
the local lock. `SIGKILL`, abort, or `execve` cannot run cleanup and may leave
the operation lock held. Later allocation, resolution, and destruction remain
fail-closed with `Busy`. A supervisor must then:

1. stop every process which can access the generation;
2. call `poison` if the mapping remains available for diagnosis;
3. discard the complete mapping rather than stealing the lock or repairing one
   slot; and
4. create a fresh mapping with a new region identity.

A paused owner is indistinguishable from a dead owner without an external
liveness protocol, so lock stealing is deliberately unsupported.

## Fixed-Address Allocator Separation

`RelocAllocator` and the Talc-backed `FixedRegionAllocator` solve different
problems. Talc supports variable-size allocation but persists pointer-bearing
metadata and therefore requires the arena at the same numeric address in every
process. `RelocAllocator` accepts fixed slot sizes and capacity in exchange for
integer-only metadata and different-address attachment. Do not attach Talc at a
different address or store Talc-backed owning collection headers as if they
were relocatable descriptors.

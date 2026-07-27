# Examples

Run these examples from the `shmem-pod` crate root. Each program is
self-checking and exits unsuccessfully if its result violates the demonstrated
contract.

## 1. Layout Handshake

`layout_handshake.rs` demonstrates the minimum safe lifecycle for typed shared
state. A child begins with untyped bytes, waits on an acquire/release ready word,
decodes the stable layout descriptor, checks size and alignment, and only then
forms `&SharedState`.

```text
cargo run --example layout_handshake
```

## 2. Relative Offsets

`relative_offsets.rs` maps the same `memfd` twice at different virtual
addresses. Both views resolve the same stored `OffsetSlice` relative to their
own `PodRegion` base and update one array of shared atomic counters.

```text
cargo run --example relative_offsets
```

## 3. Locks And Atomics

`shared_counters.rs` forks eight workers over one shared object. It applies the
same workload to a coarse table lock, four fine-grained locks, and four
lock-free atomic counters.

```text
cargo run --example shared_counters
```

`futex_mutex.rs` demonstrates the Linux blocking mutex. It spins briefly, then
uses shared futex wait/wake operations under contention.

```text
cargo run --features linux-futex --example futex_mutex
```

## 4. Scalable Presence

`snzi.rs` stresses a four-way SNZI tree from several processes. SNZI answers
whether any arrival is active; it is not an exact counter. The example creates
its sentinel token only after all forks so no live Rust token is duplicated
across a process boundary.

```text
cargo run --example snzi
```

## 5. Fixed-Address Allocation

`fixed_allocator_fork.rs` initializes Talc over caller-selected pages and
allocates concurrently after `fork`. Fork preserves the arena's address, and
every owning `Vec` header is created and destroyed within one process.

```text
cargo run --features fixed-allocator --example fixed_allocator_fork
```

`fixed_allocator_exec.rs` is the stricter demonstration. It passes a file-backed
mapping across `exec`, reserves a recorded high address with
`MAP_FIXED_NOREPLACE`, validates a bootstrap header and layout descriptor, and
attaches the allocator in each new process.

```text
cargo run --features fixed-allocator --example fixed_allocator_exec
```

The mapping examples require Linux. `fixed_allocator_exec` additionally
requires x86-64 because its demonstration address is architecture-specific.

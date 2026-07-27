# shmem-pod-macros

Derive macros for the [`shmem-pod`](https://crates.io/crates/shmem-pod)
shared-memory capability traits.

Most users should enable `shmem-pod`'s default `derive` feature and invoke the
re-exported macros:

```rust,ignore
#[derive(shmem_pod::PodValue, shmem_pod::PodSync)]
struct State {
    completed: core::sync::atomic::AtomicU64,
}
```

The derives support structs with ordinary Rust layout. They generate recursive
field capability bounds, reject fields that lack the selected capability, and
compute a structural fingerprint from the exact size, alignment, field offsets,
and transitive fingerprints. They do not create a stable Rust ABI, validate
arbitrary bytes, or audit methods and scalar semantics. Generic structs are
currently rejected because stable
Rust cannot eagerly enforce the marker traits' no-destructor contract for every
instantiation; an audited generic type needs a manual `unsafe impl`.

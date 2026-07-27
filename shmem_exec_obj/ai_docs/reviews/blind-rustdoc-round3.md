# Blind rustdoc review: `shmem-pod` 0.1.0 (round 3)

## Review conditions and inputs

I approached this as a first-time crates.io consumer with no project context. The only product material I read was generated rustdoc HTML beneath:

- `/home/newton/work/dev-hermit/shmem_exec_obj/v2/target/doc/shmem_pod/`
- `/home/newton/work/dev-hermit/shmem_exec_obj/v2/target/doc/shmem_pod_macros/`

Within those trees I used the crate landing pages and the pages for the storage/synchronization derives and traits, `ProcessFutexMutex` and its guard, `Offset`, `PodRegion`, `ResolveError`, and `LayoutDescriptor`, plus the `offset`, `sync`, and `layout` module pages. The only dependency configuration supplied to the consumer was exactly:

```toml
shmem-pod = { path = "/home/newton/work/dev-hermit/shmem_exec_obj/latest", features = ["linux-futex"] }
```

I did not read repository source, READMEs, manifests, tests, examples, history, or prior reviews. Cargo/compiler output and the executable's output were used only to build and verify the consumer. The raw Linux FFI and constants came from general platform knowledge, not project material.

## Consumer and result

I created `/tmp/shmem-pod-blind-round3-3271680`, with no direct dependency other than the supplied `shmem-pod` stanza. Its Linux executable:

1. Uses `mmap(PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS)` for one `SharedState`.
2. Defines `SharedState` with Rust's default representation (no `repr` attribute) and derives both `shmem_pod::PodValue` and `shmem_pod::PodSync`.
3. Stores a direct `AtomicU64`, `ProcessFutexMutex<CompoundTotals>`, `Offset<AtomicU64>`, and a second `AtomicU64` used as the offset target.
4. Evaluates `SharedState::FINGERPRINT`, computes the target field displacement with `std::mem::offset_of!`, constructs the entire state once at its final mapped address, and forks while no guard is live.
5. Forks three workers. Each constructs its own `PodRegion`, resolves the stored offset with `PodRegion::get`, and performs 20,000 updates to the direct atomic, resolved atomic, and mutex-protected compound fields. Each worker exits with `_exit`.
6. Waits for every worker and verifies exact totals in the parent.

**The first compile and run succeeded without any code changes.** `cargo run` built `shmem-pod` and the consumer, then printed:

```text
fingerprint=986796c78a693b726bb33c7a35355cf1
direct=60000 resolved_offset=60000
compound.updates=60000 compound.worker_id_sum=120000
```

The expected values were 60,000 updates for each mechanism and 120,000 for the compound worker-ID checksum (`20,000 * (1 + 2 + 3)`). I ran the executable three more times; every run produced the same exact totals and fingerprint.

This exercise covers inherited anonymous shared memory after `fork`; it does not test an independently mapped/exec-attached process, different virtual addresses, descriptor transport, or owner death.

## What worked well

- The crate landing page is unusually effective. It explains the representation traits, layout fingerprint, relative offsets, synchronization choices, loader responsibilities, and failure model in a useful order.
- The distinction among `PodValue`, `FixedAddressPodValue`, and `PodSync` is explicit. In particular, the statement that default Rust representation is supported because compiler-selected offsets are fingerprinted directly answered whether `#[repr(C)]` was necessary.
- The derive pages give concrete rejection criteria and explain why evaluating `FINGERPRINT` matters. Fully qualified derives worked immediately.
- `ProcessFutexMutex` documents the key process-shared facts: the lock word must be in the shared mapping, non-private futex operations are used, mapping addresses may differ, and the mutex is non-robust. The guard's fork warning is specific and actionable.
- `Offset` and `PodRegion` make the representation/runtime split clear. `PodRegion::get` documents every check it performs and why the call remains unsafe. `ResolveError` variants are detailed enough to diagnose malformed offsets.
- Trait rustdoc clearly lists support for `AtomicU64`, arrays, offsets, and the mutex. This made the derived state design predictable instead of trial-and-error.
- The feature table made `linux-futex` discovery straightforward, and the documented API signatures were sufficient for a clean first compile.

## Friction, ambiguity, and missing material

- The rustdoc points to runnable repository examples by command, but it does not put a complete mapping/fork program on the API landing page. A crates.io consumer still has to know or look up `mmap`, `fork`, `waitpid`, `_exit`, their constants, and failure conventions before reaching the crate API. That is a significant gap for the advertised multi-process use case even though mapping ownership is intentionally outside the crate.
- The relative-offset example uses an array and a hand-computed `size_of::<u64>()` displacement. There is no example showing how to create an `Offset` to a field in a default-layout derived struct. I inferred `std::mem::offset_of!(SharedState, offset_target)`; this is central enough to document, including how to account for a payload that does not begin at the region base.
- Initialization/attachment guidance is thorough for independent attachers, but the fork case is less explicit. It is unclear from the narrative whether an inherited, already typed mapping needs a descriptor-validation step in the child, whether an inherited reference may be retained, or whether consumers should deliberately reform references after fork. The safety section says to fork only while owners are quiescent, but a small fork-specific lifecycle would remove interpretation.
- `PodRegion::from_raw_parts` and `get` correctly expose unsafe contracts, but there is no end-to-end example that visibly ties mapping lifetime, typed reference lifetime, initialization, offset resolution, worker exit, guard destruction, and unmapping together. Those individually good docs leave the consumer to assemble the unsafe proof.
- The atomic guidance says cross-process behavior must be supported by the target but does not identify supported target criteria or show how a consumer should check them. The example uses `Relaxed`, while loader publication uses release/acquire; a short ordering note separating independent counters from publication would help first-time users choose intentionally.
- `shmem_pod_macros` mostly duplicates the derive documentation exported by `shmem_pod`. That is not harmful, but the public crate should remain the clearly preferred documentation destination so users do not wonder whether they must depend on the macro crate directly.

No documentation problem blocked this task, and I found no signature/documentation mismatch in the exercised surface. The successful first compile is strong evidence that the API naming, derives, bounds, and basic examples are internally coherent.

## Prioritized recommendations

1. **Add a complete, copy-paste Linux `mmap` + `fork` rustdoc example.** Show final-address initialization, a quiescent fork, at least one futex lock, child `_exit`, parent `waitpid`, and teardown. It can use `libc` in a hidden/dev dependency while reiterating that mapping policy belongs to the application.
2. **Document struct-field offset construction.** Add a default-layout example using `std::mem::offset_of!`, explain that offsets are relative to the chosen `PodRegion` base, and show the header/payload-base adjustment case. A checked helper for deriving an offset from a base and field pointer would further reduce arithmetic mistakes if it fits the API.
3. **Separate fork and independent-attach protocols explicitly.** State what references may survive fork, when descriptor validation is or is not needed for an inherited mapping, and which Rust owners/guards/tokens must be absent before forking.
4. **Provide one compact unsafe-contract checklist beside the end-to-end example.** Cover mapping identity/extent/alignment, single initialization, readiness, typed-reference formation, synchronization, child exit behavior, owner death, and teardown in execution order.
5. **Clarify the cross-process atomic target contract and ordering examples.** Name the relevant target capability assumptions and contrast relaxed counters with release/acquire publication.

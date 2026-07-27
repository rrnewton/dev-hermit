# `shmem-pod` release roadmap

## Evidence now

- Default Rust layouts work between exact-build peers: derives fingerprint the
  compiled size, alignment, field offsets, field identities, and transitive
  types. Negative compile tests reject destructors, standard owning containers,
  references, raw pointers, and ordinary mutexes.
- Pointer-free `PodValue` state works at different virtual addresses through
  checked `Offset` and `OffsetSlice` descriptors.
- Shared atomics, coarse and fine spin locking, a hybrid spin/futex mutex, and a
  four-way SNZI have exact multi-process stress tests. The futex test also execs
  a fresh process, maps one memfd at a different address, observes kernel sleep,
  and verifies wakeup.
- Talc confines allocator-aware buffers to caller-selected pages and works
  operationally across fork and fixed-address exec.
- The private image harness authenticates complete artifacts, seals immutable
  code, separates guarded RX and RW mappings, audits the x86-64 relocation
  closure, and executes a `no_std` futex slow path without a libc relocation.
- Rust 1.85 and current stable pass the full workspace, no-default, isolated
  feature, clippy, and rustdoc gates locally.
- Three blind rustdoc consumers built working examples on their first compile.

## P0: experimental `0.1` release

1. Publish only `shmem-pod-macros` and `shmem-pod`; keep image harnesses and
   injection demos private. Verify the crate names, package contents, docs.rs
   build, and the required macros-first publication sequence.
2. Automate MSRV/current, no-default, every-feature, clippy, rustdoc, package,
   and Linux process tests in release CI.
3. State the evidence envelope precisely: current runtime proof is x86-64
   Linux. Cross-process atomics, kernel facilities, endianness, target ABI, and
   other architectures need an explicit support matrix.
4. Keep layout fingerprints narrow. They detect structural compatibility; they
   do not authenticate a build or prove arbitrary bytes are a valid Rust value.
   A loader must bind the descriptor to the artifact digest, target, toolchain,
   dependency graph, and feature set.
5. Treat the Talc fixed-address tier as experimental. Equal numeric mappings
   work on the tested system, but typed pointers persisted by one process and
   dereferenced by another lack a settled Rust strict-provenance argument.
   Pointer-free offsets remain the strongest supported representation.

## P1: production-shaped API

1. Provide a typed mapping lifecycle such as `Uninitialized -> Ready ->
   Attached -> Draining`. It should own descriptor placement, single
   initialization, release/acquire publication, admission, attach validation,
   and teardown so every user does not reproduce unsafe mmap glue.
2. Add offset-owned `SharedBox`, `SharedVec`, and a relocatable allocator whose
   persistent metadata contains integers rather than typed absolute pointers.
3. Define crash semantics. Both mutexes wedge on owner death, allocator death
   can strand a lock or a half mutation, and process death can leak an SNZI
   arrival. Robust wakeup alone cannot repair a partially updated invariant;
   recovery needs poisoning, transactions/journaling, or an explicit restart
   policy.
4. Pair SNZI with a closeable admission gate so quiescence can become a real
   reclamation barrier.
5. Generate the C method table, signatures, exported entry assertions, and
   loader bindings from one `#[pod]`-style declaration. Extend the image header
   with target, endian, ABI, page-size, CPU, and hardening requirements.
6. Turn the current preload demonstration into a supported connector only
   after initialization reentrancy, trusted digest/FD transport, at-fork rules,
   unload, and failure reporting are specified. Build ptrace and binary-patch
   adapters against the same bootstrap context and C ABI.

## P2: verification and performance

- Model the futex 0/1/2 protocol and SNZI HALF transitions with Loom or an
  equivalent scheduler.
- Fuzz descriptors, offsets, image headers, relocation audits, and artifact
  parsing; run Miri on pure pointer/offset APIs and sanitizers on thread paths.
- Add kill/fault injection for initialization, lock ownership, allocation,
  attach, and teardown.
- Establish direct-call, pod-call, syscall/IPC, spin/futex, coarse/fine/atomic,
  and SNZI topology benchmarks before weakening memory orderings or tuning.
- Add AArch64 image support, including instruction-cache maintenance and
  BTI/PAC policy, plus x86 CET/IBT handling and older-kernel memfd fallbacks.
- Design state migration and rolling code upgrades after the exact-build attach
  contract is stable.
- Consider tracing collection only after shared roots, mutation barriers,
  reclamation coordination, and crash recovery are defined.

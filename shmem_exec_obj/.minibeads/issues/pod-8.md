---
title: Generate pod method ABI and strengthen image metadata
status: closed
priority: 1
issue_type: feature
assignee: devbig030/abi
depends_on:
  pod-4: blocks
  pod-1: parent-child
created_at: 2026-07-28T03:39:12.873491580+00:00
updated_at: 2026-07-28T04:58:45.813524355+00:00
closed_at: 2026-07-28T04:48:30.032728117+00:00
claimed_at: 2026-07-28T04:24:45.491230381+00:00
claimed_until: 2026-07-28T12:24:45.491063038+00:00
---

# Description

Create a #[pod]-style declaration that generates C-callable method tables, signatures, exported-entry assertions, loader bindings, and richer target/ABI/hardening metadata.

# Acceptance Criteria

A user declares a nontrivial pod without hand-synchronizing method tables; compile-fail tests reject invalid signatures; loader validates target, endian, pointer width, page size, feature/build identity, and hardening requirements.

# Notes

IMPLEMENTED in the shared working tree atop parent HEAD 41f3b94. The public #[pod] macro accepts an unsafe extern "C" declaration, requires explicit numeric IDs/export symbols, sorts methods by ID, fingerprints namespace+ID+signature independent of source order, generates typed bindings, and rejects duplicate IDs/symbols, generics, async, variadics, non-C ABIs, missing attributes, and unsupported FFI types. The demonstration has one durable method list (poc/api/src/demo_methods.rs): poc/api expands it into descriptor/bindings while no_std poc/code expands the same list into exact function-pointer assertions. The compiler emits ABI v2 metadata and the runtime resolves by ID/signature; corrupt API fingerprints and signatures fail during authenticated artifact parsing before executable mapping or transmute.

ABI v2 uses a checked 4096-byte little-endian wire header with Linux/x86_64/pointer-width/page-size/ABI revision, code+state geometry/alignment, required+optional capabilities, CPU/hardening requirements, API and state fingerprints, code/build/provenance digests, and a sorted variable method table. It rejects truncation, reserved bytes, extent overflow, duplicate/zero IDs, unknown/overlapping requirements, malformed alignments, bad target fields, and method overlap. The state envelope additionally binds API and state fingerprints.

Validation passed: `cargo check --offline --workspace --all-targets --all-features`; `cargo test --offline -p shmem-pod --all-features --test pod_api --test pod_api_ui`; `cargo test --offline -p shmem-pod-image-api -p shmem-pod-runtime -p shmem-pod-image-compiler -p shmem-pod-image-host`; `cargo clippy --offline --workspace --all-targets --all-features -- -D warnings`; `RUSTDOCFLAGS=-D warnings cargo +1.85.0 doc --locked -p shmem-pod -p shmem-pod-macros --all-features --no-deps`; `cargo +1.85.0 check --locked --workspace --all-targets --all-features`; `cargo +1.85.0 test --locked -p shmem-pod --all-features --test pod_api --test pod_api_ui`; `cargo +1.85.0 test --locked -p shmem-pod-image-api -p shmem-pod-runtime`; `POD_WORKERS=1 POD_THREADS=1 POD_ITERATIONS=5 ./scripts/run-poc.sh` (exact 10 calls/5 SNZI cycles, compiler negative fixtures all rejected); `POD_DEPTH=0 POD_FANOUT=1 POD_THREADS=1 POD_CALLS=10 ./scripts/run-preload-demo.sh` (11 exact intercepted calls, one attachment).

Residuals: image generation remains the existing direct-rustc trusted-source pipeline so its standalone relocation/provenance counterexamples remain reproducible; converting arbitrary Cargo dependency graphs/staticlibs is a separate compiler task. The image ABI intentionally supports only Linux x86_64 little-endian 64-bit today and rejects other hosts. The state fingerprint is an exact source/dependency identity supplemented by runtime size/alignment/layout-hash calls, not a claim that native Rust layout is stable across builds.

Coordinator hardening follow-up: #[pod] now rejects duplicate top-level options, duplicate per-method id/symbol keys, and empty namespaces with isolated compile-fail fixtures. Current and Rust 1.85 derive-feature checks pass.

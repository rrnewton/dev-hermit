---
title: Unify bootstrap connectors and injection adapters
status: in_progress
priority: 1
issue_type: feature
assignee: devbig030/connector-recovery
depends_on:
  pod-4: blocks
  pod-8: blocks
  pod-1: parent-child
created_at: 2026-07-28T03:39:14.009152499+00:00
updated_at: 2026-07-28T10:55:25.414618265+00:00
claimed_at: 2026-07-28T10:16:16.616866177+00:00
claimed_until: 2026-07-28T18:16:16.616695690+00:00
---

# Description

Define one allocation-free bootstrap context/C ABI for cooperative hosts, LD_PRELOAD, ptrace bootstrap, and binary-patch trampolines; harden preload lifecycle and implement practical adapters where platform support permits.

# Acceptance Criteria

Unaware guest proof uses shared connector API; trusted FD/digest transport, reentrancy, at-fork, unload/failure behavior are documented and tested; ptrace/patch support is either executable evidence or explicitly bounded with reproducible blockers.

# Notes

RECOVERY IMPLEMENTED for adversarial review at parent main 7c66d6723b02b8878087ef8e7906b8419ab66715 (status intentionally remains in_progress). Hardened fallible/coherent BootstrapContext construction, exhaustive Rust/C ABI offsets, provenance-vs-authentication contract, fail-closed panic publication, direct-bootstrap required-policy race, reentrancy, serialized at-fork disable/drain/reset, unload drain, and status classification. Added a real Linux x86-64 parent-to-child PTRACE_SEIZE/remote-dlopen/direct-bootstrap/detach proof using a pod-unaware C fixture at an explicit pipe safe point; remote DSO uses RTLD_NODELETE. Added negative digest probes for preload and ptrace. Validation at that SHA: cargo fmt --check; cargo test --locked -p shmem-pod --test bootstrap_connector -- --test-threads=1 (9 passed); cargo test --locked -p shmem-pod-preload-shim --lib -- --test-threads=1 (4 passed); cargo test --locked -p shmem-pod --no-default-features --test bootstrap_connector -- --test-threads=1 (9 passed); cargo +1.85.0 check --locked -p shmem-pod -p shmem-pod-preload-shim -p shmem-pod-preload-host --all-targets --all-features; cargo clippy --locked for those packages/all-targets/all-features with -D warnings; RUSTDOCFLAGS='-D warnings' cargo doc --locked -p shmem-pod --no-deps --all-features; C11 context_layout compile/run; run-preload-demo.sh => 7 processes, 1407 calls, 7 attachments; run-ptrace-demo.sh => target resumed after detach, 1 call, 1 attachment; test-connector-failures.sh => both rejected bad-context-digest. Documented bounds: SCM_RIGHTS receiver is not shipped and demo rejects that provenance assertion; lazy preload init is not async-signal-safe; raw fork/vfork and fork inside a hook are unsupported; ptrace proof is single-threaded, explicit-safe-point, glibc/path-layout dependent, and uses parent-inherited descriptors.

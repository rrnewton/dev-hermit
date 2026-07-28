---
title: Implement scalable closeable C-SNZI admission
status: in_progress
priority: 1
issue_type: feature
assignee: devbig030/csnzi
depends_on:
  pod-7: blocks
  pod-1: parent-child
created_at: 2026-07-28T04:45:11.434262607+00:00
updated_at: 2026-07-28T05:20:29.915023796+00:00
claimed_at: 2026-07-28T04:46:58.664800042+00:00
claimed_until: 2026-07-28T12:46:58.487033158+00:00
---

# Description

Port the SPAA 2009 closeable C-SNZI algorithm so admission close and drain do not require a global gate RMW for every token. Preserve pointer-free different-address storage, linear generation-tagged tokens, fail-closed process-death behavior, and an exact terminal seal whose final shared access cannot race payload reclamation.

# Design

Use Lev, Luchangco, and Olszewski C-SNZI as the algorithmic basis, initially with SeqCst. Parent-before-child activation must handle close-race rollback/helping and compensation. Keep the current CloseableSnzi as the simple correctness baseline unless the new implementation strictly subsumes it. The control mapping still requires an outer lifetime proof.

# Acceptance Criteria

Global root RMWs are amortized per leaf activation rather than per token; close plus zero is stable; process death at every activation/departure cut fails closed; fork ownership is explicit; Loom/model and deterministic fault-cut tests cover local/parent/root CAS and close races; benchmarks compare raw SNZI, baseline CloseableSnzi, and C-SNZI.

# Notes

IMPLEMENTATION EVIDENCE (2026-07-27, devbig030/csnzi): Implemented SPAA'2009 parent-before-child C-SNZI in v2/src/csnzi.rs with pointer-free 4-ary indices, generation-tagged linear tokens/raw ABI, SeqCst atomics, poison-on-overflow/invariant failure, one-shot close, and explicit OPEN_TAIL/CLOSED_TAIL/CLOSING terminal protocol. Same-leaf nonzero transitions do not update ancestors/root; tests hold one leaf active through 48,000 token cycles and observe root_count=1. Process tests cover different virtual addresses, fork-before-token ownership, SIGSTOP/no stealing, SIGKILL/fail-closed, close races, and raw stale/malformed tokens. Deterministic internal tests cover parent-arrival crash, child-before-parent departure crash, closing-owner crash, open/closed tail, delayed pre-close activation, compensation, capacity exhaustion, and corrupt encodings.

VALIDATION: cargo test --no-default-features --lib csnzi::tests (9 passed); cargo test --no-default-features --test csnzi (11 passed); cargo run --no-default-features --example csnzi (PASS); cargo clippy --no-default-features --test csnzi --example csnzi -- -D warnings (passed); RUSTDOCFLAGS='-D warnings' cargo doc --no-default-features --no-deps (passed); cargo +1.85.0 check --no-default-features --lib --example csnzi (passed); release unit/integration tests (9 + 11 passed); 25 repeated release close-race runs passed.

FREESTANDING RUST 1.85: Built examples/csnzi.rs under --cfg csnzi_freestanding with the supported opt=3/panic=abort/PIC/small-code-model/no-unwind flags and linked using poc/code/pod.ld. rustc 1.85.0 4d91de4e4 emitted 102 PC32 + 7 PLT32 input relocations, zero R_X86_64_64 absolute relocations, one 6,192-byte VMA-zero .pod in an R-E PT_LOAD, no undefined global or memcpy/memset/allocator/pthread/futex symbol, and no call instruction in shmem_pod_init. Extracted RX code executed init -> same-leaf enter twice -> query -> close -> post-close rejection -> depart twice -> drained against separate RW state: PASS freestanding RX closure bytes=6192 state=1408 align=64. Exact commands/results are durable in v2/docs/csnzi.md. This audit found and fixed a rustc 1.85 panic_bounds_check/absolute-relocation edge using one audited internal node pointer helper.

RESIDUAL PROOF ASSUMPTIONS: Rust process-shared atomics are an audited Linux/hardware deployment assumption, not a complete Rust abstract-machine guarantee; safe use requires exact-once token ownership, authenticated identical code/layout fingerprints, exclusive initialization, and no byte corruption; payload access must end before depart; terminal drain permits payload reclamation but the C-SNZI control pages need an outer attachment/fencing lifetime; process death intentionally leaks and never lease-steals; 47-bit generations and packed counters poison rather than wrap; tests/model cuts are not a mechanized linearizability proof. Benchmark comparison against Snzi and baseline CloseableSnzi remains for coordinator-owned benchmark/release wiring. Bead intentionally left in_progress for fresh adversarial review.

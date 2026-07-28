---
title: Define state migration, reclamation, and tracing-GC boundary
status: in_progress
priority: 2
issue_type: task
assignee: devbig030/migration-reclamation
depends_on:
  pod-7: blocks
  pod-4: blocks
  pod-8: blocks
  pod-6: blocks
  pod-1: parent-child
  pod-5: blocks
created_at: 2026-07-28T03:39:42.092157584+00:00
updated_at: 2026-07-28T10:40:14.279277217+00:00
claimed_at: 2026-07-28T05:19:32.486714861+00:00
claimed_until: 2026-07-30T05:19:32.486511533+00:00
---

# Description

Design rolling code/state upgrades and reclamation after close/drain; evaluate whether tracing collection is justified once roots, mutation barriers, crash recovery, and transactions exist.

# Acceptance Criteria

Version negotiation and migration protocol has executable tests for at least one schema upgrade; reclamation is fenced by admission/quiescence; GC is implemented only if its safety prerequisites are met, otherwise a concrete non-GC policy is documented.

# Notes

IMPLEMENTED (awaiting coordinator review): added v2/src/migration.rs, tests/migration.rs, examples/schema_migration.rs, and docs/migration-and-reclamation.md. Protocol uses exact version+layout schema identities, one-shot source-drain -> private target copy -> TargetReady -> atomic Committed route switch -> fenced Reclaimed cleanup. It composes with CloseableSnzi, typed Mapping Draining/ClosedMapping, RelocAllocator, SharedBox, and fresh region generations; no repr(C) is required. Owner death never steals/resumes a partial copy; SIGKILL leaves Copying fail-closed, metadata/phase corruption poisons, and Reclaimed supports idempotent cleanup resume. Tracing GC is rejected until roots, trusted trace metadata, enforced mutation barriers, process safepoints, and crash-consistent mark/free transactions exist; explicit object destruction or whole-generation discard is documented. Evidence on 2026-07-27: cargo test --manifest-path v2/Cargo.toml -p shmem-pod --test migration -- --test-threads=1 (5 passed, includes different-address mapping, SIGKILL, drain and schema upgrade); cargo test --manifest-path v2/Cargo.toml -p shmem-pod --lib migration::tests -- --test-threads=1 (2 corruption tests passed); cargo run --manifest-path v2/Cargo.toml -p shmem-pod --example schema_migration (PASS); cargo check --manifest-path v2/Cargo.toml -p shmem-pod --no-default-features --lib (pass); cargo +1.85.0 check --manifest-path v2/Cargo.toml -p shmem-pod --all-targets --all-features (pass); cargo clippy --manifest-path v2/Cargo.toml -p shmem-pod --all-targets --all-features -- -D warnings (pass); RUSTDOCFLAGS=-D warnings cargo doc --manifest-path v2/Cargo.toml -p shmem-pod --all-features --no-deps (pass); cargo test --manifest-path v2/Cargo.toml -p shmem-pod --doc (4 passed); cargo package --allow-dirty --list contains all four assets. Do not close until root review.

2026-07-28 adversarial migration correction: replaced momentary drain checks with consumed sealed quiescence capabilities (AdmissionQuiescence / MappingQuiescence); begin_with_quiescent_source exact-compares schema+region+monotonic sequence+authenticated backing digest before claiming. Added GenerationIdentity, BackingIdentity, AuthorityIdentity and plan validation for nonzero identity material, distinct source/target region/backing, increasing supervisor-supplied sequence, and increasing schema; docs explicitly state region IDs alone are not freshness evidence and cross-restart monotonicity is a supervisor obligation. Added unsafe PrecommitTargetBacking type-state: mark_target_ready consumes an exact/private target capability bound to the plan's independently live recovery authority, TargetReadyMigration owns source+target through the commit CAS, and CommittedMigration returns them only after commit. Strengthened unsafe target-ready contract for exact schema/region/backing, complete roots/descriptors, happens-before publication, no interior/raw writers, and coherent shared memory. Scope now explicitly excludes machine crash, reboot, power loss, filesystem/persistent-memory durability. Counterexamples cover unrelated drained source rejection before claim, terminal admission/non-reopen, consumed ClosedMapping safe-attach rejection, target generation/authority mismatch, prepublished target rejection, within-plan region/backing collisions, and nonmonotonic sequence. Evidence: cargo test --locked -p shmem-pod --test migration -- --test-threads=1 (7 passed); cargo test --locked -p shmem-pod --lib migration::tests -- --test-threads=1 (2 passed); cargo run --locked -p shmem-pod --example schema_migration (PASS); cargo check --locked -p shmem-pod --no-default-features --lib (pass); cargo +1.85.0 check --locked -p shmem-pod --all-targets --all-features (pass); cargo clippy --locked -p shmem-pod --all-targets --all-features -- -D warnings (pass); RUSTDOCFLAGS=-D warnings cargo doc --locked -p shmem-pod --all-features --no-deps (pass); cargo test --locked -p shmem-pod --doc (5 passed); cargo package --locked --allow-dirty --list contains all four migration assets. Full all-feature suite ran 48 preceding tests (including migration unit tests) and then hit unrelated derive_ui diagnostic-string drift (*const Target vs compiler's *const case_fixed_pointer::Target); focused migration integration separately passed 7/7 and no migration test failed. Leave in_progress for fresh adversarial re-review.

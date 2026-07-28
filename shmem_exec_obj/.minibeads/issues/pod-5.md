---
title: Implement relocatable allocator and shared collections
status: closed
priority: 0
issue_type: feature
assignee: devbig030/allocator
depends_on:
  pod-1: parent-child
  pod-4: blocks
created_at: 2026-07-28T03:39:09.627177172+00:00
updated_at: 2026-07-28T05:18:40.656396271+00:00
closed_at: 2026-07-28T05:18:40.656395891+00:00
claimed_at: 2026-07-28T04:24:45.486376951+00:00
claimed_until: 2026-07-28T12:24:45.486202398+00:00
---

# Description

Add offset-based allocator metadata and useful SharedBox/SharedVec primitives that work when one backing object is mapped at different virtual addresses, without persisted typed absolute pointers.

# Acceptance Criteria

Independent exec processes map at different addresses and safely allocate/read/update/free shared objects; corruption, overflow, stale offset, and concurrency cases are tested; Talc fixed-address tier remains clearly separated.

# Notes

Implemented v2/src/reloc_allocator.rs, v2/src/collections.rs, integration tests, relocatable_collections example, and relocatable-allocation guide. Persistent references contain integer region/slot/generation/offset/geometry/fingerprint fields only; attachment validates full allocator metadata before pointer formation; safe reads require PodSync; mutation/destruction remain explicit unsafe lifecycle operations. Validation: cargo test --locked -p shmem-pod --test reloc_allocator --test shared_collections -- --test-threads=1 passed 10 allocator and 4 collection tests, including independent exec mappings at different addresses, model/concurrency tests, SIGKILL poisoning, stale generation, descriptor corruption, and canonical-null regression. Fresh adversarial source review reported no blocker or major finding; the sole low canonical-null gap was fixed and regressed.

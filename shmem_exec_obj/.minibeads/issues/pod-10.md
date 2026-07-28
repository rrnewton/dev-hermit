---
title: Add model, fuzz, sanitizer, and fault-injection validation
status: in_progress
priority: 1
issue_type: task
assignee: devbig030/validation
depends_on:
  pod-9: blocks
  pod-7: blocks
  pod-4: blocks
  pod-1: parent-child
  pod-6: blocks
  pod-5: blocks
  pod-17: blocks
  pod-8: blocks
created_at: 2026-07-28T03:39:15.028398087+00:00
updated_at: 2026-07-28T12:56:15.305052172+00:00
claimed_at: 2026-07-28T12:56:15.305052172+00:00
claimed_until: 2026-07-30T12:56:15.304817016+00:00
---

# Description

Exercise synchronization and parsers beyond ordinary unit tests using model checking where feasible, deterministic race tests, fuzz targets/corpora, Miri/sanitizers where supported, and kill/fault injection.

# Acceptance Criteria

Protocols and parsers have automated adversarial coverage; initialization, lock ownership, allocation, attach, and teardown faults cannot silently violate documented invariants; unsupported tools are recorded precisely.

# Notes

Admission fault/model scope: deterministically kill an entrant after gate reservation, kill a drain checker after persisting CHECKING, and explore entry/close/depart/seal interleavings. Verify no false drain and document expected fail-closed wedges. C-SNZI adds fault cuts at every local, parent, and root CAS plus close rollback/help/compensation.

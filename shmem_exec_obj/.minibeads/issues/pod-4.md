---
title: Implement a typed shared mapping lifecycle
status: closed
priority: 0
issue_type: feature
assignee: devbig030/mapping
depends_on:
  pod-1: parent-child
created_at: 2026-07-28T03:39:08.553812597+00:00
updated_at: 2026-07-28T04:24:37.876181617+00:00
closed_at: 2026-07-28T04:24:37.876180846+00:00
claimed_at: 2026-07-28T03:48:57.636526220+00:00
claimed_until: 2026-07-28T09:48:57.636373098+00:00
---

# Description

Replace repeated unsafe mmap/bootstrap glue with typed Uninitialized, Ready, Attached, Draining lifecycle states that own descriptor placement, single initialization, publication, attachment validation, admission, and teardown.

# Acceptance Criteria

Public API has documented safety invariants and negative/positive multi-process tests including initialization races, descriptor mismatch, attach after exec, draining, and guarded mappings.

# Notes

Final reviewed implementation is commit 6de61a36b810a3df5f9e4d8f83f508a13702683e (with earlier construction commits in its ancestry). Evidence: Rust 1.85 all-feature mapping lifecycle 13/13, private corruption tests 2/2, conditional-Send UI test on MSRV/current, typed_mapping PASS, different-VA exec, process initializer races, kill/fail-stuck, guard pages, in-place unwind poison. Two adversarial mapping reviews found no remaining core issue.

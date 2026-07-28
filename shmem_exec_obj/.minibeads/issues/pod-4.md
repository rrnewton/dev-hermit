---
title: Implement a typed shared mapping lifecycle
status: in_progress
priority: 0
issue_type: feature
assignee: devbig030/mapping
depends_on:
  pod-1: parent-child
created_at: 2026-07-28T03:39:08.553812597+00:00
updated_at: 2026-07-28T03:48:57.636526220+00:00
claimed_at: 2026-07-28T03:48:57.636526220+00:00
claimed_until: 2026-07-28T09:48:57.636373098+00:00
---

# Description

Replace repeated unsafe mmap/bootstrap glue with typed Uninitialized, Ready, Attached, Draining lifecycle states that own descriptor placement, single initialization, publication, attachment validation, admission, and teardown.

# Acceptance Criteria

Public API has documented safety invariants and negative/positive multi-process tests including initialization races, descriptor mismatch, attach after exec, draining, and guarded mappings.

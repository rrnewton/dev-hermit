---
title: Add closeable admission and SNZI crash policy
status: in_progress
priority: 1
issue_type: feature
assignee: devbig030/admission
depends_on:
  pod-6: blocks
  pod-1: parent-child
  pod-4: blocks
created_at: 2026-07-28T03:39:11.862719796+00:00
updated_at: 2026-07-28T04:28:29.916229986+00:00
claimed_at: 2026-07-28T04:28:29.916229986+00:00
claimed_until: 2026-07-28T10:28:29.916033700+00:00
---

# Description

Combine SNZI with an admission gate and define behavior for process death so quiescence can be used as a reclamation barrier.

# Acceptance Criteria

No new arrivals occur after close; drain reaches a well-defined state; token leak/crash behavior is detected or recoverable by documented policy; multi-process race and kill tests pass.

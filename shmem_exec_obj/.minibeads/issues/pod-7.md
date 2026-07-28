---
title: Add closeable admission and SNZI crash policy
status: open
priority: 1
issue_type: feature
depends_on:
  pod-1: parent-child
  pod-4: blocks
  pod-6: blocks
created_at: 2026-07-28T03:39:11.862719796+00:00
updated_at: 2026-07-28T03:39:55.167348290+00:00
---

# Description

Combine SNZI with an admission gate and define behavior for process death so quiescence can be used as a reclamation barrier.

# Acceptance Criteria

No new arrivals occur after close; drain reaches a well-defined state; token leak/crash behavior is detected or recoverable by documented policy; multi-process race and kill tests pass.

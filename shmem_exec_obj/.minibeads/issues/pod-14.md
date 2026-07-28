---
title: Polish public documentation examples and release artifacts
status: open
priority: 1
issue_type: task
depends_on:
  pod-12: blocks
  pod-8: blocks
  pod-4: blocks
  pod-1: parent-child
  pod-13: blocks
  pod-10: blocks
  pod-2: blocks
  pod-6: blocks
  pod-7: blocks
  pod-3: blocks
  pod-9: blocks
  pod-11: blocks
  pod-5: blocks
created_at: 2026-07-28T03:39:43.219735911+00:00
updated_at: 2026-07-28T03:46:38.901154092+00:00
---

# Description

Make the latest tree read like an independent crates.io library: tutorial, conceptual model, safety/failure contracts, complete examples, package metadata, changelog/release checklist, and no project-internal context.

# Acceptance Criteria

Fresh package docs cover every public type and expected workflow; examples are ordered and runnable; crates package/verify independently; docs contain no stale measurements or private-project assumptions.

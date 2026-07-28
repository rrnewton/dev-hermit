---
title: Define state migration, reclamation, and tracing-GC boundary
status: open
priority: 2
issue_type: task
depends_on:
  pod-5: blocks
  pod-4: blocks
  pod-6: blocks
  pod-1: parent-child
  pod-7: blocks
  pod-8: blocks
created_at: 2026-07-28T03:39:42.092157584+00:00
updated_at: 2026-07-28T03:40:19.155436835+00:00
---

# Description

Design rolling code/state upgrades and reclamation after close/drain; evaluate whether tracing collection is justified once roots, mutation barriers, crash recovery, and transactions exist.

# Acceptance Criteria

Version negotiation and migration protocol has executable tests for at least one schema upgrade; reclamation is fenced by admission/quiescence; GC is implemented only if its safety prerequisites are met, otherwise a concrete non-GC policy is documented.

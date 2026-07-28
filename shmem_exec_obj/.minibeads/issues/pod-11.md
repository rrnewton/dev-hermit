---
title: Build reproducible IPC and synchronization benchmarks
status: in_progress
priority: 2
issue_type: task
assignee: devbig030
depends_on:
  pod-9: blocks
  pod-7: blocks
  pod-6: blocks
  pod-1: parent-child
  pod-5: blocks
  pod-4: blocks
  pod-8: blocks
  pod-17: blocks
created_at: 2026-07-28T03:39:16.035270684+00:00
updated_at: 2026-07-28T10:35:38.427417925+00:00
claimed_at: 2026-07-28T10:35:38.427417925+00:00
claimed_until: 2026-07-28T18:35:38.227858288+00:00
---

# Description

Measure direct calls, pod calls, syscall/IPC baselines, spin/futex/recoverable locks, coarse/fine/atomic counters, SNZI topologies, and allocator/collection operations.

# Acceptance Criteria

Benchmark harness emits machine-readable results with environment metadata and validates totals; docs distinguish latency/throughput tradeoffs without making unsupported performance claims.

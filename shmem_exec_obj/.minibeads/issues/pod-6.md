---
title: Design and implement recoverable cross-process locks
status: in_progress
priority: 0
issue_type: feature
assignee: devbig030/locks
depends_on:
  pod-1: parent-child
created_at: 2026-07-28T03:39:10.745287201+00:00
updated_at: 2026-07-28T03:48:57.640608704+00:00
claimed_at: 2026-07-28T03:48:57.640608704+00:00
claimed_until: 2026-07-28T09:48:57.640457505+00:00
---

# Description

Research robust futexes, process-shared robust pthread mutexes, owner identity, pidfd, timeouts, leases, fencing tokens, poison/transaction recovery, and implement the strongest sound primitive supported by the pod constraints.

# Acceptance Criteria

A cited design explains failure semantics; waits are bounded/configurable; paused-owner and PID-reuse hazards are addressed; owner death tests prove either recovery with fencing/poison or explicit fail-closed behavior; no timeout alone grants unsafe duplicate ownership.

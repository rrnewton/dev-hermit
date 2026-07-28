---
title: Add closeable admission and SNZI crash policy
status: closed
priority: 1
issue_type: feature
assignee: devbig030/admission
depends_on:
  pod-1: parent-child
  pod-6: blocks
  pod-4: blocks
created_at: 2026-07-28T03:39:11.862719796+00:00
updated_at: 2026-07-28T04:46:35.155311736+00:00
closed_at: 2026-07-28T04:46:35.155311515+00:00
claimed_at: 2026-07-28T04:28:29.916229986+00:00
claimed_until: 2026-07-28T10:28:29.916033700+00:00
---

# Description

Combine SNZI with an admission gate and define behavior for process death so quiescence can be used as a reclamation barrier.

# Acceptance Criteria

No new arrivals occur after close; drain reaches a well-defined state; token leak/crash behavior is detected or recoverable by documented policy; multi-process race and kill tests pass.

# Notes

Implemented pointer-free CloseableSnzi with exact close/entry ordering, transient publication and full-departure-tail reservations, CHECKING/DRAINED terminal sealing, fail-closed poison, explicit linear/raw tokens, in-place initialization, fingerprints, crash/fork policy, and literature guide. Validated no-default unit/doc/clippy/rustdoc, close races, SIGSTOP no-steal, SIGKILL token leak, and forked example. Independent adversarial review found no false-drain execution under stated model after exact terminal-word and anti-starvation fixes. Control pages still require an outer lifetime proof; C-SNZI scalability and exhaustive fault/model testing are tracked in pod-17/pod-10.

---
title: Design and implement recoverable cross-process locks
status: closed
priority: 0
issue_type: feature
assignee: devbig030/locks
depends_on:
  pod-1: parent-child
created_at: 2026-07-28T03:39:10.745287201+00:00
updated_at: 2026-07-28T05:23:58.161814810+00:00
closed_at: 2026-07-28T04:28:08.223044892+00:00
claimed_at: 2026-07-28T03:48:57.640608704+00:00
claimed_until: 2026-07-28T09:48:57.640457505+00:00
---

# Description

Research robust futexes, process-shared robust pthread mutexes, owner identity, pidfd, timeouts, leases, fencing tokens, poison/transaction recovery, and implement the strongest sound primitive supported by the pod constraints.

# Acceptance Criteria

A cited design explains failure semantics; waits are bounded/configurable; paused-owner and PID-reuse hazards are addressed; owner death tests prove either recovery with fencing/poison or explicit fail-closed behavior; no timeout alone grants unsafe duplicate ownership.

# Notes

Final implementation commit c15efdd after adversarial portability review. Literature and precedents are in v2/docs/locking.md. Evidence: Rust 1.85 lock suite 10/10; full workspace clippy/rustdoc; different-VA exec, EINTR count, timeout/unlock boundary, SIGSTOP no-steal, SIGKILL fail-stuck, Duration::MAX; i686/AArch64/SPARC64 cross checks; ordinary and forced-timed direct-rustc POC relocation closure. Timed API intentionally supports only audited Linux 64-bit x86_64/AArch64; no timeout grants ownership.

Follow-up literature audit: Gray/Cheriton leases rely on an authoritative server that withholds conflicts until approval/expiry; Chubby uses sequencers/fencing at the protected resource; Golab/Ramaraju recoverable mutual exclusion assumes failed participants re-enter an explicit recovery section and may require critical-section re-entry. These precedents reinforce the implemented API split: futex timeout is cancellation only, generic mutable Rust state is never lease-stolen, and any future lease API must fence each externally visible commit or use type-specific idempotent recovery. docs/locking.md now records this distinction and citations.

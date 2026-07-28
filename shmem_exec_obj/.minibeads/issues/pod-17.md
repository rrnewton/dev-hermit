---
title: Implement scalable closeable C-SNZI admission
status: in_progress
priority: 1
issue_type: feature
assignee: devbig030/csnzi
depends_on:
  pod-1: parent-child
  pod-7: blocks
created_at: 2026-07-28T04:45:11.434262607+00:00
updated_at: 2026-07-28T04:46:58.664800042+00:00
claimed_at: 2026-07-28T04:46:58.664800042+00:00
claimed_until: 2026-07-28T12:46:58.487033158+00:00
---

# Description

Port the SPAA 2009 closeable C-SNZI algorithm so admission close and drain do not require a global gate RMW for every token. Preserve pointer-free different-address storage, linear generation-tagged tokens, fail-closed process-death behavior, and an exact terminal seal whose final shared access cannot race payload reclamation.

# Design

Use Lev, Luchangco, and Olszewski C-SNZI as the algorithmic basis, initially with SeqCst. Parent-before-child activation must handle close-race rollback/helping and compensation. Keep the current CloseableSnzi as the simple correctness baseline unless the new implementation strictly subsumes it. The control mapping still requires an outer lifetime proof.

# Acceptance Criteria

Global root RMWs are amortized per leaf activation rather than per token; close plus zero is stable; process death at every activation/departure cut fails closed; fork ownership is explicit; Loom/model and deterministic fault-cut tests cover local/parent/root CAS and close races; benchmarks compare raw SNZI, baseline CloseableSnzi, and C-SNZI.

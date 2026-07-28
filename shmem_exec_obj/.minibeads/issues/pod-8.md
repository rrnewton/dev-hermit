---
title: Generate pod method ABI and strengthen image metadata
status: in_progress
priority: 1
issue_type: feature
assignee: devbig030/abi
depends_on:
  pod-4: blocks
  pod-1: parent-child
created_at: 2026-07-28T03:39:12.873491580+00:00
updated_at: 2026-07-28T04:24:45.491230381+00:00
claimed_at: 2026-07-28T04:24:45.491230381+00:00
claimed_until: 2026-07-28T12:24:45.491063038+00:00
---

# Description

Create a #[pod]-style declaration that generates C-callable method tables, signatures, exported-entry assertions, loader bindings, and richer target/ABI/hardening metadata.

# Acceptance Criteria

A user declares a nontrivial pod without hand-synchronizing method tables; compile-fail tests reject invalid signatures; loader validates target, endian, pointer width, page size, feature/build identity, and hardening requirements.

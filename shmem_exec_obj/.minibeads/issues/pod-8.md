---
title: Generate pod method ABI and strengthen image metadata
status: open
priority: 1
issue_type: feature
depends_on:
  pod-4: blocks
  pod-1: parent-child
created_at: 2026-07-28T03:39:12.873491580+00:00
updated_at: 2026-07-28T03:39:56.267943212+00:00
---

# Description

Create a #[pod]-style declaration that generates C-callable method tables, signatures, exported-entry assertions, loader bindings, and richer target/ABI/hardening metadata.

# Acceptance Criteria

A user declares a nontrivial pod without hand-synchronizing method tables; compile-fail tests reject invalid signatures; loader validates target, endian, pointer width, page size, feature/build identity, and hardening requirements.

---
title: Expand architecture and hardening support
status: open
priority: 2
issue_type: feature
depends_on:
  pod-8: blocks
  pod-9: blocks
  pod-10: blocks
  pod-1: parent-child
created_at: 2026-07-28T03:39:17.102115881+00:00
updated_at: 2026-07-28T03:40:13.760110694+00:00
---

# Description

Validate or implement AArch64 image generation/cache maintenance and BTI/PAC policy, x86 CET/IBT handling, syscall ABI variants, and older-kernel/memfd fallbacks.

# Acceptance Criteria

Support matrix is backed by cross-build/static inspection and runtime evidence where hardware exists; unsupported combinations fail closed at load time rather than executing incompatible images.

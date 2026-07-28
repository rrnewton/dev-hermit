---
title: Expand architecture and hardening support
status: open
priority: 2
issue_type: feature
depends_on:
  pod-10: blocks
  pod-1: parent-child
  pod-9: blocks
  pod-8: blocks
created_at: 2026-07-28T03:39:17.102115881+00:00
updated_at: 2026-07-28T04:53:15.943718578+00:00
---

# Description

Validate or implement AArch64 image generation/cache maintenance and BTI/PAC policy, x86 CET/IBT handling, syscall ABI variants, and older-kernel/memfd fallbacks.

# Acceptance Criteria

Support matrix is backed by cross-build/static inspection and runtime evidence where hardware exists; unsupported combinations fail closed at load time rather than executing incompatible images.

# Notes

Hardening audit must distinguish parsing known requirement bits from actually satisfying them. Loader should compute host/runtime capabilities (W^X/NX state, CPU features, CET/IBT or AArch64 BTI/PAC/cache maintenance, page size/kernel facilities) and reject any required bit not proven. Build/provenance identities need an explicit trusted expected-value or documented derivation from the externally trusted complete artifact digest.

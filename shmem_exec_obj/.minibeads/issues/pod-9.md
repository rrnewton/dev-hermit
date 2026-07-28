---
title: Unify bootstrap connectors and injection adapters
status: open
priority: 1
issue_type: feature
depends_on:
  pod-8: blocks
  pod-4: blocks
  pod-1: parent-child
created_at: 2026-07-28T03:39:14.009152499+00:00
updated_at: 2026-07-28T03:39:58.347342088+00:00
---

# Description

Define one allocation-free bootstrap context/C ABI for cooperative hosts, LD_PRELOAD, ptrace bootstrap, and binary-patch trampolines; harden preload lifecycle and implement practical adapters where platform support permits.

# Acceptance Criteria

Unaware guest proof uses shared connector API; trusted FD/digest transport, reentrancy, at-fork, unload/failure behavior are documented and tested; ptrace/patch support is either executable evidence or explicitly bounded with reproducible blockers.

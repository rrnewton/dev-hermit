---
title: Make derive UI diagnostics stable across supported rustc
status: closed
priority: 1
issue_type: bug
assignee: devbig030/ui
depends_on:
  pod-3: discovered-from
  pod-1: parent-child
created_at: 2026-07-28T04:01:05.035738899+00:00
updated_at: 2026-07-28T04:03:20.446237797+00:00
closed_at: 2026-07-28T04:03:20.446237557+00:00
claimed_at: 2026-07-28T04:01:05.046453302+00:00
claimed_until: 2026-07-28T06:01:05.046297677+00:00
---

# Description

The compile-fail harness matches rustc-rendered fully-qualified type text that changed between Rust 1.85 and current stable. Match stable semantic fragments without weakening rejection coverage.

# Acceptance Criteria

All negative derive cases pass on Rust 1.85 and current stable; expectations still identify the intended trait/safety failure.

# Notes

Implemented in f16d7ac by compiling every invalid fixture independently and matching case-local semantic fragments. Passed cargo +1.85.0 test --locked -p shmem-pod --test derive_ui and current cargo test --locked -p shmem-pod --test derive_ui.

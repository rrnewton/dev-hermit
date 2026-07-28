---
title: Make derive UI diagnostics stable across supported rustc
status: in_progress
priority: 1
issue_type: bug
assignee: devbig030/ui
depends_on:
  pod-1: parent-child
  pod-3: discovered-from
created_at: 2026-07-28T04:01:05.035738899+00:00
updated_at: 2026-07-28T04:01:05.046453302+00:00
claimed_at: 2026-07-28T04:01:05.046453302+00:00
claimed_until: 2026-07-28T06:01:05.046297677+00:00
---

# Description

The compile-fail harness matches rustc-rendered fully-qualified type text that changed between Rust 1.85 and current stable. Match stable semantic fragments without weakening rejection coverage.

# Acceptance Criteria

All negative derive cases pass on Rust 1.85 and current stable; expectations still identify the intended trait/safety failure.

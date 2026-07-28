---
title: Automate release gates and define the support envelope
status: in_progress
priority: 1
issue_type: task
assignee: devbig030/release
depends_on:
  pod-1: parent-child
  pod-16: blocks
created_at: 2026-07-28T03:39:07.527879286+00:00
updated_at: 2026-07-28T04:01:05.041061889+00:00
claimed_at: 2026-07-28T03:48:57.632065284+00:00
claimed_until: 2026-07-28T09:48:57.631909789+00:00
---

# Description

Provide a reproducible release-check entry point covering MSRV/current, feature combinations, clippy, rustdoc, package contents, process tests, and documented Linux/architecture support.

# Acceptance Criteria

One documented command runs every locally available release gate; failures are bounded; package manifests exclude private harnesses; support matrix and publication sequence are explicit.

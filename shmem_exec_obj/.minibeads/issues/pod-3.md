---
title: Automate release gates and define the support envelope
status: open
priority: 1
issue_type: task
depends_on:
  pod-1: parent-child
created_at: 2026-07-28T03:39:07.527879286+00:00
updated_at: 2026-07-28T03:39:07.527879286+00:00
---

# Description

Provide a reproducible release-check entry point covering MSRV/current, feature combinations, clippy, rustdoc, package contents, process tests, and documented Linux/architecture support.

# Acceptance Criteria

One documented command runs every locally available release gate; failures are bounded; package manifests exclude private harnesses; support matrix and publication sequence are explicit.

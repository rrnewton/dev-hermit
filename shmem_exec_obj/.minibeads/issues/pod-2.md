---
title: Establish minibeads workflow and blind-review protocol
status: open
priority: 0
issue_type: task
depends_on:
  pod-1: parent-child
created_at: 2026-07-28T03:39:06.513236799+00:00
updated_at: 2026-07-28T03:39:06.513236799+00:00
---

# Description

Add directory-local AGENTS.md instructions for minibeads and a reusable project-local skill that defines a source-blind packaged-library audit.

# Acceptance Criteria

Agents are instructed to run mb quickstart, claim/update/close issues without hand-editing issue files, and invoke the blind review skill before release completion.

---
title: Establish minibeads workflow and blind-review protocol
status: closed
priority: 0
issue_type: task
assignee: devbig030
depends_on:
  pod-1: parent-child
created_at: 2026-07-28T03:39:06.513236799+00:00
updated_at: 2026-07-28T03:48:39.673891939+00:00
closed_at: 2026-07-28T03:48:39.673891698+00:00
claimed_at: 2026-07-28T03:47:28.159804484+00:00
claimed_until: 2026-07-28T07:47:28.159593565+00:00
---

# Description

Add directory-local AGENTS.md instructions for minibeads and a reusable project-local skill that defines a source-blind packaged-library audit.

# Acceptance Criteria

Agents are instructed to run mb quickstart, claim/update/close issues without hand-editing issue files, and invoke the blind review skill before release completion.

# Notes

Implemented by commit 70fa66b. Added AGENTS.md minibead workflow plus .llms/skills/shmem-pod-blind-review/SKILL.md with source isolation, required consumer exercises, severity rules, and iterative acceptance.

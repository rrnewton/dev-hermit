---
title: Implement relocatable allocator and shared collections
status: open
priority: 0
issue_type: feature
depends_on:
  pod-4: blocks
  pod-1: parent-child
created_at: 2026-07-28T03:39:09.627177172+00:00
updated_at: 2026-07-28T03:39:52.996337860+00:00
---

# Description

Add offset-based allocator metadata and useful SharedBox/SharedVec primitives that work when one backing object is mapped at different virtual addresses, without persisted typed absolute pointers.

# Acceptance Criteria

Independent exec processes map at different addresses and safely allocate/read/update/free shared objects; corruption, overflow, stale offset, and concurrency cases are tested; Talc fixed-address tier remains clearly separated.

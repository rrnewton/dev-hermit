---
name: core-memory-parked-slot-reuse-is-racy
description: "reusing a detached/clean/not-in-ACTIVE.md slot is racy — another agent can switch its branch mid-work; only use an explicitly-assigned slot (CORE-MEMORY mirror of memory/parked-slot-reuse-is-racy.md)"
---

# CORE-MEMORY: parked-slot-reuse-is-racy

<!-- GENERATED MIRROR of core memory `parked-slot-reuse-is-racy`. Source of truth is the memory
     file `parked-slot-reuse-is-racy.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in
     sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the
     markers — edit the memory and re-run sync. -->

<!-- BEGIN CORE-MEMORY-MIRROR (source: parked-slot-reuse-is-racy.md) -->
Reusing a "parked-looking" hermit slot (detached HEAD, clean, absent from
worktrees/ACTIVE.md) is NOT safe under concurrent agents. Observed 2026-07-23:
fell back to slot84 after the assigned slot02 was occupied; created branch
impl-new-e2e-tests, wrote+built+tested files, but another agent `git switch`ed
slot84 to their branch `fix-mt-virtual-time-divergence` BETWEEN my `switch -c`
and my `git commit` — so my commit landed on THEIR branch and my `push` created
an empty branch. Cleanup: `git reset --mixed <their-tip>` (removes my commit,
preserves their uncommitted WIP), rm my untracked files, delete my empty
local+remote branches; verify the slot is back to their state.

**Why:** ACTIVE.md is not honored by every agent (the one churning slot84 never
listed it), and a worktree's checked-out branch is shared mutable state; a
concurrent `switch`/`reset` silently moves it under you. Detached+clean is a
necessary but NOT sufficient signal of "free."

**How to apply:** work ONLY in a slot the coordinator explicitly assigned to
you (and confirm it's clean + on the expected branch right before AND after each
git op). If the assigned slot is occupied, do NOT grab an arbitrary parked slot
— report the conflict and request an exclusive assignment or a fresh
`./slot-init.sh slotNN hermit`. Preserve verified work outside any repo checkout
so a collision never loses it. Relates to [[git-stash-shared-across-worktrees]].
<!-- END CORE-MEMORY-MIRROR -->

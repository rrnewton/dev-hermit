---
name: core-memory-git-stash-shared-across-worktrees
description: "git stash is shared across all worktrees in dev-hermit; never use it with concurrent agents (CORE-MEMORY mirror of memory/git-stash-shared-across-worktrees.md)"
---

# CORE-MEMORY: git-stash-shared-across-worktrees

<!-- GENERATED MIRROR of core memory `git-stash-shared-across-worktrees`. Source of truth is the memory
     file `git-stash-shared-across-worktrees.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in
     sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the
     markers — edit the memory and re-run sync. -->

<!-- BEGIN CORE-MEMORY-MIRROR (source: git-stash-shared-across-worktrees.md) -->
In `~/work/dev-hermit`, all slots under `worktrees/slotNN` are git worktrees of
ONE repository, so they share a single stash stack (`refs/stash` in the common
`.git`). Multiple agents run concurrently.

**Why:** `git stash pop`/`git stash` with no explicit ref operate on
`stash@{0}`, which another agent may have just pushed. A plain `git stash pop`
popped a *different* agent's stash into my slot and dropped its stack entry —
mixing foreign changes into my tree and nearly losing theirs.

**How to apply:** Do NOT use `git stash` / `git stash pop` for save/restore in a
slot. To compare against baseline, instead: commit WIP to the feature branch and
`git checkout <base> -- <files>` or use `git worktree`/a temp branch, or copy
files aside. If a stash is unavoidable, always reference it by its commit SHA
(`git stash list --format='%gd %H %gs'`, then `git stash apply <sha>`), never by
index. Recover a wrongly-dropped stash by tagging its dangling commit
(`git tag <name> <sha>`) — dropped stash commits survive until GC.
<!-- END CORE-MEMORY-MIRROR -->

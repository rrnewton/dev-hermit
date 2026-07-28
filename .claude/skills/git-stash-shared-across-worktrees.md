---
name: git-stash-shared-across-worktrees
description: "git stash is shared across all worktrees in dev-hermit; never use it with concurrent agents"
---

In the parent workspace, all slots under `worktrees/slotNN` are git worktrees of
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

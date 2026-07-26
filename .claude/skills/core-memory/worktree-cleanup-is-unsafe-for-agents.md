---
name: core-memory-worktree-cleanup-is-unsafe-for-agents
description: "Don't mass-delete worktrees; ACTIVE.md is stale, many have uncommitted work, coordinator owns slot lifecycle (CORE-MEMORY mirror of memory/worktree-cleanup-is-unsafe-for-agents.md)"
---

# CORE-MEMORY: worktree-cleanup-is-unsafe-for-agents

<!-- GENERATED MIRROR of core memory `worktree-cleanup-is-unsafe-for-agents`. Source of truth is the memory
     file `worktree-cleanup-is-unsafe-for-agents.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in
     sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the
     markers — edit the memory and re-run sync. -->

<!-- BEGIN CORE-MEMORY-MIRROR (source: worktree-cleanup-is-unsafe-for-agents.md) -->
Mass worktree/slot deletion is NOT safe for a single agent to perform; surface an audit and let the coordinator drive removal.

**Why:** As of 2026-07-22 the dev-hermit checkout had ~142 registered git worktrees (130 hermit + 12 reverie). Concrete hazards found (task impl-cleanup-worktrees):
- 13 worktrees had UNCOMMITTED changes — deleting loses work irreversibly.
- AGENTS.md: "Do not delete permanent slot worktrees; their build caches are intentionally reusable"; "Creating slots is a coordinator operation."
- `worktrees/ACTIVE.md` is an UNRELIABLE "unassigned" oracle: the clean-and-not-in-ACTIVE.md set included slot96 & slot97, which had OPEN PRs #211/#216. So "not in ACTIVE.md" ≠ safe to delete.
- Nothing was git-prunable (`git worktree prune --dry-run` empty; all dirs exist) — so no zero-risk admin cleanup either.

**How to apply:** For a worktree-cleanup request, do a read-only audit only (`git worktree list`, per-worktree `git status --porcelain`, ACTIVE.md cross-ref, `prune --dry-run`) and post it. Remove nothing unless ALL hold: clean tree AND branch fully merged to origin/main (or detached at a main-reachable commit) AND owning agent confirmed done AND outside the numbered slot pool. Never trust ACTIVE.md alone. This matches the operating rule: don't delete something you didn't create that contradicts its description — surface it. Relates to [[git-stash-shared-across-worktrees]], [[never-git-stash-shared-worktrees]].
<!-- END CORE-MEMORY-MIRROR -->

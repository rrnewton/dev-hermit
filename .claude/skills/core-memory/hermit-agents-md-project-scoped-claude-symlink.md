---
name: core-memory-hermit-agents-md-project-scoped-claude-symlink
description: "hermit/AGENTS.md is now a single-project guide (no workspace discipline); hermit/CLAUDE.md is a symlink to it; safe way to push doc-only to hermit main (CORE-MEMORY mirror of memory/hermit-agents-md-project-scoped-claude-symlink.md)"
---

# CORE-MEMORY: hermit-agents-md-project-scoped-claude-symlink

<!-- GENERATED MIRROR of core memory `hermit-agents-md-project-scoped-claude-symlink`. Source of truth is the memory
     file `hermit-agents-md-project-scoped-claude-symlink.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in
     sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the
     markers — edit the memory and re-run sync. -->

<!-- BEGIN CORE-MEMORY-MIRROR (source: hermit-agents-md-project-scoped-claude-symlink.md) -->
hermit/AGENTS.md was refocused as a clean single-project developer guide (commit 9a492fe on rrnewton/hermit main, 2026-07-23). ALL multi-agent workspace/worktree discipline (slots, ACTIVE.md/ARCHIVED.md, parking, coordinator ops, slot-init.sh, handoff/completion-report formats) was REMOVED — that content belongs at the dev-hermit coordinator level, not the hermit project repo. Kept: overview, environment, build/test, lint/format, Cargo workspace map, architecture (Reverie/Detcore + ptrace/DBI/KVM backends), L0-L4 assurance ladder, debugging, change guidelines, contributing/PR. If you need the old worktree/slot rules, look in dev-hermit/AGENTS.md, not here.

`hermit/CLAUDE.md` is a **symlink → AGENTS.md** — never edit CLAUDE.md separately; editing AGENTS.md updates both.

SAFE doc-only push to hermit main when the shared ~/work/dev-hermit/hermit worktree is on a feature branch with other agents' dirt: do NOT switch its branch or stash. Instead `git -C ~/work/dev-hermit/hermit worktree add --detach $HOME/tmp-wt origin/main`, edit+commit there, `with-proxy git push origin HEAD:main` (ff), then `git worktree remove --force`. hermit main is unprotected; direct doc push is fine when the task authorizes it. Related: [[never-git-stash-shared-worktrees]], [[base-feature-branches-on-frontier]] (main-only now).
<!-- END CORE-MEMORY-MIRROR -->

---
name: core-memory-base-feature-branches-on-frontier
description: "dev-hermit is now MAIN-ONLY — frontier branch deleted 2026-07-22; base all work on origin/main (CORE-MEMORY mirror of memory/base-feature-branches-on-frontier.md)"
---

# CORE-MEMORY: base-feature-branches-on-frontier

<!-- GENERATED MIRROR of core memory `base-feature-branches-on-frontier`. Source of truth is the memory
     file `base-feature-branches-on-frontier.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in
     sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the
     markers — edit the memory and re-run sync. -->

<!-- BEGIN CORE-MEMORY-MIRROR (source: base-feature-branches-on-frontier.md) -->
**SUPERSEDED 2026-07-22:** The `frontier` branch has been DELETED on both
`rrnewton/hermit` and `rrnewton/reverie` (remote), by user directive to move to a
main-only workflow. Do NOT base work on `origin/frontier` — it no longer exists.
Base all feature branches on `origin/main` and open PRs with `--base main`.

Main has since absorbed the work that used to live only on frontier (rr_suite,
`third-party/rr` submodule, strict-syscall handlers via #207/#208, statfs/fstatfs
#209, etc.). The primary checkouts `hermit/` and `reverie/` now track `main`; the
old `./main/` rebase-base directory was removed. AGENTS.md updated: worktree base
is `main`, layout no longer lists `./main/`.

Recovery (if frontier is ever needed): the pre-deletion tips were
hermit `bedac4d7ea7f82c0d96190b9904375950763a250`, reverie
`70f83e7ce2104e1b986e095c476bbc81be163d2e` — re-pushable via
`git push origin <sha>:refs/heads/frontier`.

**Why:** Basing on a deleted branch fails immediately; the whole backlog of open
PRs was closed and the workflow flipped to direct-to-main.
**How to apply:** `git -C worktrees/slotNN switch -c <branch> origin/main`;
`gh pr create --base main --draft`. See [[frontier-already-complete-dont-force-push]]
and [[frontier-diverges-on-reverie-fork]] — both now obsolete.
<!-- END CORE-MEMORY-MIRROR -->

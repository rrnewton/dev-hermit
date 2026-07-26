---
name: core-memory-undraft-does-not-trigger-ci
description: "gh pr ready does NOT enqueue CI on rrnewton/hermit; ci.yml lacks ready_for_review trigger (CORE-MEMORY mirror of memory/undraft-does-not-trigger-ci.md)"
---

# CORE-MEMORY: undraft-does-not-trigger-ci

<!-- GENERATED MIRROR of core memory `undraft-does-not-trigger-ci`. Source of truth is the memory
     file `undraft-does-not-trigger-ci.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in
     sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the
     markers — edit the memory and re-run sync. -->

<!-- BEGIN CORE-MEMORY-MIRROR (source: undraft-does-not-trigger-ci.md) -->
`.github/workflows/ci.yml` triggers on `pull_request: branches: [main]` with the
**default activity types only** (opened/synchronize/reopened) plus push to
main/frontier. `ready_for_review` is NOT listed, so `gh pr ready <N>` (undraft)
does **not** enqueue any CI run — including the saturated self-hosted lane.

Consequence: undrafting validated draft PRs is safe and does not add queue load.
This removes the queue-flooding objection when converting drafts to
ready-for-review. Also: only base=`main` PRs get PR-CI at all; frontier-based
PRs (common here, see [[base-feature-branches-on-frontier]]) get no pull_request
CI, only push-to-frontier CI. Separately, the self-hosted lane is main-wide red
(see [[self-hosted-ci-sigsegv-blocks-all-prs]]).
<!-- END CORE-MEMORY-MIRROR -->

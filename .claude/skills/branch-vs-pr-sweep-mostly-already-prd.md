---
name: branch-vs-pr-sweep-mostly-already-prd
description: "How to sweep today's branches for missing PRs; most 'un-PR'd' branches are superseded/stale, not orphaned work"
---

Coordinator sweep for "completed feature branches lacking PRs" (task
`impl-open-prs-today-work`, 2026-07-25). Reusable method + finding:

**Method:** enumerate local branches ahead of `origin/main` in hermit
(shared across worktrees) and reverie, filter by `committerdate` = target day,
subtract the **all-states** PR-head set (`gh pr list --state all --json headRefName`,
~388 hermit / ~96 reverie) plus helper patterns (`land-*`, `*-rebase`,
`archive/*`, `frontier*`, `*-adopt`). Then triage each survivor by its **tip
commit's own diff** (`git show <tip>`), NOT `origin/main..branch` — stale-based
branches show main's advance as thousands of phantom "deletions" (e.g. validate.sh
-884, whole dirs deleted). Check merge-base staleness (`rev-list --count mb..origin/main`).

**Finding (the premise is usually FALSE):** nearly all "un-PR'd" survivors are
one of: (a) SUPERSEDED by a merged PR (same crate/files already on main — verify
with `git ls-tree origin/main` + `gh pr list --search "<title> in:title"`), (b)
OWNED in-progress work in an active slot (see [[parked-slot-reuse-is-racy]],
[[detached-clean-merged-slot-can-be-busy]]) — do not poach, (c) stale rebase/CI
helper variants, or (d) stale-frontier (frontier is deleted, see
[[base-feature-branches-on-frontier]]). Real orphaned completed work is rare.

The one genuine case this sweep found: coordinated pair
`fix-dbi-pid-virtualization-slot113` (reverie DBI PID/TID virtualization
`native/client.c` + hermit procfs virtual-PID normalization + L2 regression) —
net-new, pushed to origin on both repos, no PR, no active owner. Published as
DRAFT reverie#106 + hermit#723 (cross-linked). Deliberately NOT labeled
`locally-validated`: base was 39/11 commits stale (predates #712 validate.sh
restructure), so a head-gate would false-bless. Land reverie dep first, repin,
then validate consumer — see cross-repo rule. Don't apply the gate label to a
stale-based draft.

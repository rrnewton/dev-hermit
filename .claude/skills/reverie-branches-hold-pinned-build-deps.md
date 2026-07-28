---
name: reverie-branches-hold-pinned-build-deps
description: "Deleting rrnewton/reverie branches can orphan a hermit-pinned build dep; check containment first"
---

Before deleting branches on rrnewton/reverie, check every reverie `?rev=SHA` that hermit pins (grep `reverie.git?rev=` across all Cargo.lock/Cargo.toml files below the parent workspace) against branch containment (`git branch -r --contains <sha>` in a reverie checkout). Hermit pins reverie by exact SHA, not by branch name, so a "stale" feature branch can be the ONLY ref keeping a live pinned commit reachable — deleting it orphans the commit and breaks `cargo` fresh builds (GitHub won't serve an unreachable SHA).

**Why:** During the 2026-07-22 reverie branch cleanup, `impl-rcx-r11-canonicalization` was the sole holder of rev `f6bcc06e498...`, pinned by 5 active worktrees (slot84/89/90/94/96). "Delete all but main + open PRs" would have broken those builds.

**How to apply:** Keep any branch that uniquely holds a pinned rev (or re-pin the dependent Cargo.locks to a rev on main first). Also note: reverie `frontier` is a speculative rollup, not pinned by name; its content rev is on main, so it's build-safe to drop but holds unmerged merge-commits — record the tip SHA before deleting so it's recoverable. Branch deletion on GitHub is recoverable short-term (restore from PR page / push the retained local ref). See [[frontier-diverges-on-reverie-fork]], [[dbi-client-rev-e3e2c965-broken]].

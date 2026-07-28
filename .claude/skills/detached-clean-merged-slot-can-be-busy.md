---
name: detached-clean-merged-slot-can-be-busy
description: "A hermit worktree slot that is detached+clean+merged can STILL be actively in use — agents run in detached HEAD with work in /tmp chroots, so the slot looks idle between operations. Always check for live processes (cwd) before removing. Also two schemes of slotNN dirs exist."
---

Task impl-worktree-cleanup (2026-07-23). Refines
[[worktree-cleanup-is-unsafe-for-agents]] and [[parked-slot-reuse-is-racy]].

The git-state signal "DETACHED + dirty=0 + HEAD ancestor of origin/main" is
NECESSARY but NOT SUFFICIENT to call a slot dead. Observed slot14, slot31,
slot100 all detached+clean+merged **yet with live processes** — agents run
hermit in detached HEAD and do their real work in `/tmp/.tmpXXXX` chroot copies,
so the slot's own tree reads clean/merged *between* syscalls. Removing on git
state alone destroys active work.

SAFE removal rule I used (all must hold, RE-VERIFIED immediately before each
`git worktree remove` WITHOUT --force): detached + dirty=0 + not in ACTIVE.md +
HEAD is ancestor of origin/main + **zero live processes with cwd in the slot**
(scan /proc/*/cwd, match `worktrees/slotNN`, include /tmp chroot copies whose
path ends in the slot). Removed slot48/51/89/95 this way; ~14 slots were busy.

TWO slotNN schemes under `worktrees/` (don't conflate): (a) hermit-submodule
worktrees (.git→hermit/.git) = the real "hermit slots" the cleanup targets;
(b) parent-superproject worktrees slot01-12 & slot72-demos (.git→dev-hermit/.git,
branches on the legacy lead namespace) = OUT OF SCOPE. `git -C hermit worktree list` only
shows (a); `git worktree list` from the parent shows (b).

PROCESS-KILL HAZARD: killing leaked hermit processes by command-pattern is
dangerous under concurrency — multiple agents run near-identical
`hermit record ... <site-node> -e console.log(42)` commands. Distinguish
yours by extra flags (--data-dir/--log-file) and cwd. hermit ptrace tracers
ignore SIGTERM; need SIGKILL. Only kill trees matching your EXACT signature.

**How to apply:** the "max 12 slots" target is NOT safely achievable while ~14
slots are live; do a single safe sweep (state + no-live-proc, re-verified, no
--force), log removals to ARCHIVED.md, and leave the rest for the coordinator in
a quiescent window. This task has now been worked 4× with the same conservative
outcome — that IS the correct outcome, not under-delivery.

# release-worktree.rs prune-bound-to-remove-success — mutation verification

## Question

Commit `7c61509` ("release-worktree: bind worktree prune to remove success")
claims to stop `release-worktree.rs --clean` from orphaning a slot's on-disk data
dir when `git worktree remove` fails. **Does the code actually prevent orphaning
under a failed remove, and do N normal releases still fully clean up?** A commit
subject is a claim; this experiment is the mutation-level evidence.

## Background — the orphaning mechanism

`git worktree remove <path>` deletes two artifacts: the worktree **data dir** and
the primary's **admin entry** (`.git/.../worktrees/<id>`). A partial failure (the
observed trigger was a **transient btrfs EROFS/IO stall** on `liteinst2`, not a
persistent read-only mount) can delete/break the worktree's `.git` gitdir link
early while data files remain on disk. The worktree is then **prune-eligible**
(`git worktree prune` reaps entries whose gitdir link is broken/missing). The old
code ran `git worktree prune` **unconditionally** after the loop, so prune reaped
the admin entry while the data stayed on disk → **orphaned** (untracked bytes no
git command references). A transient fault + a non-idempotent cleanup is strictly
worse than either alone: recoverable residue becomes a permanent artifact.

The fix (subject file `scripts/release-worktree.rs`, lines 452–500): prune runs
**only** after this product's remove returned ok, or when the data dir is already
gone (nothing to orphan). On remove failure the loop skips prune, retains
admin+data together, and exits non-zero with a retry instruction.

## Method

`mutation-test.sh` builds isolated `/tmp` git repos (no shared parent/primary/slot
state touched) and runs three independent checks, A/B-comparing the BUGGY
(unconditional prune) vs FIXED (prune bound to remove success) control flow
against **real git**:

1. **MECHANISM** — inject the exact state a partial EROFS remove leaves (worktree
   `.git` link removed, `DATA` file still on disk; confirmed prune-eligible via
   `prune --dry-run`). BUGGY: unconditional prune → data orphaned. FIXED: skip
   prune → admin+data retained together.
2. **CONTROLFLOW** — a genuinely failing `git worktree remove` (locked worktree,
   no `--force`) drives the same ok/!ok branch the fix hinges on; assert the
   FIXED branch skips prune and admin+data are intact.
3. **NORMAL** — **N=5** successful `remove → prune` cycles on clean worktrees;
   assert each fully removes the data dir *and* the admin entry.

## Results

All checks PASS (harness exit 0; see `run.log`, `results.csv`):

| check | arm | outcome |
|-------|-----|---------|
| mechanism | buggy | unconditional prune **ORPHANS** data (reproduces bug) |
| mechanism | fixed | skip-prune **RETAINS** admin+data (recoverable) |
| controlflow | fixed | real remove failure → prune skipped, admin+data intact |
| normal | fixed | **5/5** releases fully removed worktree + admin entry |

## Interpretation

The fix is verified by mutation, not inference: the buggy sequence provably
orphans on-disk data on a partial-failure state, and the fixed sequence retains it
recoverably while the failed-remove control flow is exercised against real git.
Normal releases are unaffected (5/5 fully clean). The orphaning bug is closed.

## Reproduction

```
bash mutation-test.sh   # exits 0 on success; self-contained, uses /tmp only
```

Requires `git` (verified on 2.53.0-Meta) and `bash`. No network, no shared state.
